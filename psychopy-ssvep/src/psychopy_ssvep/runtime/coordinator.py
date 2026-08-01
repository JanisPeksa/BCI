from __future__ import annotations

import threading
from enum import StrEnum
from typing import Callable

from psychopy_ssvep.acquisition.service import AcquisitionService
from psychopy_ssvep.config.loader import ResolvedExperiment
from psychopy_ssvep.events.models import EventType, ProtocolEvent
from psychopy_ssvep.planning.compiler import compile_session_plan
from psychopy_ssvep.recording.session import SessionRecorder, validate_session
from psychopy_ssvep.runtime.clock import Clock, RealClock
from psychopy_ssvep.runtime.commands import (
    AbortSession,
    CloseRequested,
    PresentationFrameAcknowledged,
    RuntimeCommand,
)
from psychopy_ssvep.runtime.protocol import ProtocolRuntime, RunState, TERMINAL_STATES


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

    def emit(self, event: ProtocolEvent) -> None:
        coordinator = self.coordinator
        coordinator.recorder.emit(event)
        coordinator.acquisition.emit(event)


class SessionCoordinator:
    def __init__(
        self,
        resolved: ResolvedExperiment,
        participant_id: str,
        session_label: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.resolved = resolved
        self.clock = clock or RealClock()
        self.plan = compile_session_plan(resolved.config)
        self.recorder = SessionRecorder(resolved, self.plan, participant_id, session_label)
        self._pending_error: Exception | None = None
        self._error_lock = threading.Lock()
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
        try:
            self.acquisition.stop()
        except Exception as exc:
            cleanup_errors.append(str(exc))
        background_error = self._take_background_error()
        if background_error is not None:
            cleanup_errors.append(str(background_error))
        if cleanup_errors:
            status = "failed"
            error = "; ".join(filter(None, (error, *cleanup_errors)))
        self.recorder.finalize(status, error)
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
