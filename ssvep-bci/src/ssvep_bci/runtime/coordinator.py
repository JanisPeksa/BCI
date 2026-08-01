from __future__ import annotations

import threading
from enum import StrEnum
from typing import Callable

from ssvep_bci.acquisition.service import AcquisitionService
from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.dsp.contracts import ProcessingResult, ProcessingStatus
from ssvep_bci.dsp.windows import SampleRingBuffer, WindowRequest
from ssvep_bci.dsp.worker import DspService
from ssvep_bci.events.models import EventType, ProtocolEvent
from ssvep_bci.outputs import NullOutputSink, OutputSink, SelectionDecision
from ssvep_bci.planning.compiler import compile_session_plan
from ssvep_bci.recording.session import SessionRecorder, validate_session
from ssvep_bci.runtime.clock import Clock, RealClock
from ssvep_bci.runtime.commands import (
    AbortSession,
    CloseRequested,
    PresentationFrameAcknowledged,
    RuntimeCommand,
)
from ssvep_bci.runtime.protocol import ProtocolRuntime, RunState, TERMINAL_STATES
from ssvep_bci.runtime.verification import VerificationSnapshot, VerificationTracker


class CoordinatorState(StrEnum):
    CREATED = "created"
    PRE_ROLL = "pre_roll"
    RUNNING = "running"
    POST_ROLL = "post_roll"
    FINALIZED = "finalized"
    FAILED = "failed"


class _EventRouter:
    def __init__(self, coordinator: "SessionCoordinator") -> None:
        self.coordinator = coordinator
        self._onsets: dict[str, ProtocolEvent] = {}

    def emit(self, event: ProtocolEvent) -> None:
        coordinator = self.coordinator
        coordinator.recorder.emit(event)
        coordinator.acquisition.emit(event)
        if event.event_type == EventType.STIMULUS_ONSET and event.presentation_id:
            self._onsets[event.presentation_id] = event
        elif event.event_type == EventType.STIMULUS_OFFSET and event.presentation_id:
            onset = self._onsets.pop(event.presentation_id, None)
            if onset is not None and event.payload.get("confirmed_by_frame_swap"):
                coordinator.dsp.submit(WindowRequest(onset=onset, offset=event))


class SessionCoordinator:
    def __init__(
        self,
        resolved: ResolvedExperiment,
        participant_id: str,
        session_label: str | None = None,
        clock: Clock | None = None,
        output_sink: OutputSink | None = None,
        verification: bool = False,
    ) -> None:
        self.resolved = resolved
        self.clock = clock or RealClock()
        self.plan = compile_session_plan(resolved.config)
        self.verification = verification
        if verification and resolved.config.processing.processor != "fbtdca":
            raise ValueError("verification mode requires the fbtdca processor")
        max_window = (
            resolved.config.protocol.stimulation_seconds
            + resolved.config.processing.window.wait_timeout_seconds
            + 5.0
        )
        self.ring = SampleRingBuffer(
            len(resolved.device.channels), max_window, resolved.device.sampling_rate_hz
        )
        self._pending_error: Exception | None = None
        self._error_lock = threading.Lock()
        self.output = output_sink or NullOutputSink()
        self.dsp = DspService(
            resolved,
            self.ring,
            on_result=self._on_processing_result,
            on_error=self._on_background_error,
            participant_id=participant_id,
        )
        self.verification_tracker: VerificationTracker | None = None
        if verification:
            processor = self.dsp.processor
            if (
                resolved.classifier_path is None
                or processor.model_metadata is None
                or processor.model_sha256 is None
            ):
                raise ValueError("verification mode requires compatible model metadata")
            self.verification_tracker = VerificationTracker(
                self.plan,
                resolved.config,
                resolved.classifier_path,
                processor.model_sha256,
                processor.model_metadata,
            )
        self.recorder = SessionRecorder(
            resolved,
            self.plan,
            participant_id,
            session_label,
            run_mode="verification" if verification else "run",
        )
        self.acquisition = AcquisitionService(
            resolved,
            self.clock,
            on_batch=self._on_batch,
            on_marker=self.recorder.record_marker,
            on_error=self._on_background_error,
        )
        self.router = _EventRouter(self)
        self.runtime = ProtocolRuntime(
            self.recorder.session_id,
            self.plan,
            resolved.config,
            self.clock,
            self.router,
        )
        self.state = CoordinatorState.CREATED
        self._deadline: float | None = None
        self.validation_report: dict | None = None
        self._finalized_callbacks: list[Callable[[], None]] = []

    @property
    def session_path(self):
        return self.recorder.path

    def add_finalized_callback(self, callback: Callable[[], None]) -> None:
        self._finalized_callbacks.append(callback)

    def start(self) -> None:
        if self.state != CoordinatorState.CREATED:
            raise RuntimeError("coordinator has already started")
        try:
            descriptor = self.acquisition.start()
            self.recorder.set_acquisition_descriptor(descriptor)
            self.dsp.start()
            self.state = CoordinatorState.PRE_ROLL
            self._deadline = (
                self.clock.monotonic()
                + self.resolved.config.protocol.acquisition_pre_roll_seconds
            )
        except Exception as exc:
            self.runtime.fail(str(exc))
            self._finalize("failed", str(exc))
            raise

    def tick(self) -> None:
        error = self._take_background_error()
        if error is not None and self.state not in {
            CoordinatorState.FINALIZED, CoordinatorState.FAILED
        }:
            self.runtime.fail(str(error))
            self.state = CoordinatorState.POST_ROLL
            self._deadline = self.clock.monotonic()
        if self.state == CoordinatorState.PRE_ROLL:
            assert self._deadline is not None
            if self.clock.monotonic() >= self._deadline:
                self.runtime.start()
                self.state = CoordinatorState.RUNNING
        if self.state == CoordinatorState.RUNNING:
            self.runtime.tick()
            if self.runtime.state in TERMINAL_STATES:
                self.state = CoordinatorState.POST_ROLL
                self._deadline = (
                    self.clock.monotonic()
                    + self.resolved.config.protocol.acquisition_post_roll_seconds
                )
        if self.state == CoordinatorState.POST_ROLL:
            assert self._deadline is not None
            if self.clock.monotonic() >= self._deadline:
                status = {
                    RunState.COMPLETED: "complete",
                    RunState.ABORTED: "aborted",
                    RunState.FAILED: "failed",
                }.get(self.runtime.state, "incomplete")
                self._finalize(status, self.runtime.error)

    def execute(self, command: RuntimeCommand) -> None:
        if isinstance(command, PresentationFrameAcknowledged):
            self.runtime.acknowledge_frame(command)
        elif isinstance(command, (AbortSession, CloseRequested)):
            self.runtime.abort(command.reason)
        else:  # pragma: no cover
            raise TypeError(f"unsupported runtime command: {type(command).__name__}")

    def close(self) -> None:
        if self.state in {CoordinatorState.FINALIZED, CoordinatorState.FAILED}:
            return
        self.runtime.abort("application closed")
        self._finalize("aborted")

    def _on_batch(self, batch) -> None:
        try:
            self.recorder.record_batch(batch)
            self.ring.append(batch)
        except Exception as exc:
            self._on_background_error(exc)

    def _on_processing_result(self, result: ProcessingResult) -> None:
        try:
            self.recorder.record_processing(result)
            if self.verification_tracker is not None:
                entry = self.verification_tracker.record(result)
                self.recorder.record_verification(entry)
            if result.status != ProcessingStatus.PROCESSED:
                if self.resolved.config.processing.required:
                    self._on_background_error(RuntimeError(
                        f"required DSP window {result.window_id} was not processed: "
                        f"{result.status.value}"
                    ))
                return
            if result.predicted_index is not None and result.predicted_frequency_hz is not None:
                self.output.emit(SelectionDecision(
                    session_id=self.recorder.session_id,
                    presentation_id=result.presentation_id,
                    predicted_index=result.predicted_index,
                    predicted_frequency_hz=result.predicted_frequency_hz,
                    scores=result.scores,
                ))
        except Exception as exc:
            self._on_background_error(exc)

    def _on_background_error(self, error: Exception) -> None:
        with self._error_lock:
            if self._pending_error is None:
                self._pending_error = error

    def _take_background_error(self) -> Exception | None:
        with self._error_lock:
            value = self._pending_error
            self._pending_error = None
            return value

    def _finalize(self, status: str, error: str | None = None) -> None:
        if self.state in {CoordinatorState.FINALIZED, CoordinatorState.FAILED}:
            return
        cleanup_errors: list[str] = []
        for operation in (self.acquisition.stop, self.dsp.stop, self.output.close):
            try:
                operation()
            except Exception as exc:
                cleanup_errors.append(str(exc))
        background_error = self._take_background_error()
        if background_error is not None:
            cleanup_errors.append(str(background_error))
        if cleanup_errors:
            status = "failed"
            error = "; ".join(filter(None, (error, *cleanup_errors)))
        summary = (
            self.verification_tracker.summary(status)
            if self.verification_tracker is not None
            else None
        )
        self.recorder.finalize(status, error, verification_summary=summary)
        try:
            self.validation_report = validate_session(self.recorder.path)
        except Exception as exc:
            self.validation_report = None
            status = "failed"
            error = f"session validation failed: {exc}"
            self.recorder.mark_failed_after_validation(error)
        self.state = (
            CoordinatorState.FAILED if status == "failed" else CoordinatorState.FINALIZED
        )
        for callback in self._finalized_callbacks:
            callback()

    def verification_snapshot(self) -> VerificationSnapshot | None:
        if self.verification_tracker is None:
            return None
        return self.verification_tracker.snapshot()
