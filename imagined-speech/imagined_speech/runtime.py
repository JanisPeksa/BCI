"""Session lifecycle and experimenter-command orchestration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from imagined_speech.acquisition import (
    AcquisitionRecorder,
    AcquisitionSnapshot,
    create_acquisition_backend,
)
from imagined_speech.config import ResolvedExperiment
from imagined_speech.engine import (
    ProtocolClock,
    ProtocolEngine,
    RealClock,
    RunState,
    TERMINAL_STATES,
)
from imagined_speech.events import CompositeEventSink, EventSource, MemoryEventSink
from imagined_speech.operator import (
    OperatorCommand,
    OperatorCommandRecord,
    OperatorCommandStatus,
)
from imagined_speech.plan import SessionPlan, compile_session_plan
from imagined_speech.session import SessionValidationReport, SessionWriter, validate_session


class SessionRuntimeState(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    PRE_ROLL = "pre_roll"
    RUNNING = "running"
    POST_ROLL = "post_roll"
    FINALIZED = "finalized"
    FAILED = "failed"


class SessionRuntime:
    """Own one configured session from acquisition startup through validation."""

    def __init__(
        self,
        resolved: ResolvedExperiment,
        participant_id: str,
        *,
        output_root: Path | None = None,
        session_label: str | None = None,
        clock: ProtocolClock | None = None,
    ) -> None:
        self.resolved = resolved
        self.clock = clock or RealClock()
        self.plan: SessionPlan = compile_session_plan(resolved.config)
        self.writer = SessionWriter(
            resolved,
            self.plan,
            participant_id,
            output_root,
            auto_finalize=False,
            session_label=session_label,
        )
        self.event_memory = MemoryEventSink()
        backend = create_acquisition_backend(resolved, self.clock)
        self.acquisition = AcquisitionRecorder(self.writer.path, backend, self.clock)
        self.engine = ProtocolEngine(
            self.writer.session_id,
            self.plan,
            resolved.config,
            self.clock,
            CompositeEventSink(self.acquisition, self.writer, self.event_memory),
        )
        self.state = SessionRuntimeState.CREATED
        self.validation_report: SessionValidationReport | None = None
        self.operator_records: list[OperatorCommandRecord] = []
        self.error: str | None = None
        self._roll_deadline: float | None = None
        self._finalized = False

    @property
    def session_path(self) -> Path:
        return self.writer.path

    @property
    def recording(self) -> bool:
        return self.acquisition.running

    def start(self) -> None:
        if self.state != SessionRuntimeState.CREATED:
            raise RuntimeError(f"cannot start runtime from {self.state.value}")
        self.state = SessionRuntimeState.CONNECTING
        try:
            self.acquisition.start()
            self.state = SessionRuntimeState.PRE_ROLL
            self._roll_deadline = (
                self.clock.monotonic() + self.resolved.device.pre_roll_seconds
            )
        except Exception as exc:
            self.error = str(exc)
            self.engine.fail(str(exc))
            self._finalize("failed")
            self.state = SessionRuntimeState.FAILED
            raise

    def tick(self) -> None:
        if self.state == SessionRuntimeState.PRE_ROLL:
            assert self._roll_deadline is not None
            if self.clock.monotonic() >= self._roll_deadline:
                self.engine.start()
                self.state = SessionRuntimeState.RUNNING
        if self.state == SessionRuntimeState.RUNNING:
            self.engine.tick()
            if self.engine.state in TERMINAL_STATES:
                self.state = SessionRuntimeState.POST_ROLL
                self._roll_deadline = (
                    self.clock.monotonic() + self.resolved.device.post_roll_seconds
                )
        if self.state == SessionRuntimeState.POST_ROLL:
            assert self._roll_deadline is not None
            if self.clock.monotonic() >= self._roll_deadline:
                status = {
                    RunState.COMPLETED: "complete",
                    RunState.ABORTED: "aborted",
                    RunState.FAILED: "failed",
                }.get(self.engine.state, "incomplete")
                self._finalize(status)
                self.state = (
                    SessionRuntimeState.FAILED
                    if status == "failed"
                    else SessionRuntimeState.FINALIZED
                )

    def execute(
        self,
        command: OperatorCommand,
        *,
        note: str | None = None,
        source: str = EventSource.EXPERIMENTER_UI.value,
    ) -> OperatorCommandRecord:
        if self._finalized:
            raise RuntimeError("cannot record a command after session finalization")
        before = self.engine.state
        context = self.engine.current_action.context if self.engine.current_action else None
        status = OperatorCommandStatus.ACCEPTED
        reason = f"{command.value} accepted"
        try:
            event_source = EventSource.EXPERIMENTER_UI
            if command == OperatorCommand.PAUSE:
                self.engine.pause(event_source)
            elif command == OperatorCommand.RESUME:
                self.engine.resume(event_source)
            elif command == OperatorCommand.REPEAT_TRIAL:
                self.engine.repeat_current_trial(event_source)
            elif command == OperatorCommand.REPEAT_BLOCK:
                self.engine.repeat_current_block(event_source)
            elif command == OperatorCommand.REFIT:
                if self.engine.state == RunState.RUNNING:
                    self.engine.pause(event_source)
                self.engine.record_refit(note or "", event_source)
            elif command == OperatorCommand.ABORT:
                self.engine.abort(event_source)
            else:  # pragma: no cover - exhaustive StrEnum guard
                raise ValueError(f"unsupported operator command: {command}")
        except (RuntimeError, ValueError) as exc:
            status = OperatorCommandStatus.REJECTED
            reason = str(exc)

        record = self.writer.record_operator_command(
            command=command,
            status=status,
            source=source,
            monotonic_seconds=self.clock.monotonic(),
            wall_time_utc=self.clock.wall_time_utc(),
            reason=reason,
            note=note.strip() if note and note.strip() else None,
            state_before=before.value,
            resulting_state=self.engine.state.value,
            block_id=context.block_id if context else None,
            trial_id=context.trial_id if context else None,
            attempt=context.attempt if context else None,
            payload={"runtime_state": self.state.value},
        )
        self.operator_records.append(record)
        return record

    def acquisition_snapshot(self, max_samples: int = 750) -> AcquisitionSnapshot:
        return self.acquisition.snapshot(max_samples)

    def close(self) -> None:
        if self._finalized:
            return
        if self.engine.state in {RunState.RUNNING, RunState.PAUSED}:
            self.execute(
                OperatorCommand.ABORT,
                note="Experimenter application closed",
            )
        status = {
            RunState.COMPLETED: "complete",
            RunState.ABORTED: "aborted",
            RunState.FAILED: "failed",
        }.get(self.engine.state, "incomplete")
        self._finalize(status)
        self.state = (
            SessionRuntimeState.FAILED
            if status == "failed"
            else SessionRuntimeState.FINALIZED
        )

    def _finalize(self, status: str) -> None:
        if self._finalized:
            return
        self.acquisition.stop()
        for artifact in self.acquisition.artifact_names:
            if (self.writer.path / artifact).is_file():
                self.writer.register_artifact(artifact)
        self.writer.finalize(status)
        self._finalized = True
        try:
            self.validation_report = validate_session(self.writer.path)
        except Exception as exc:
            self.error = f"session validation failed: {exc}"
            if status == "complete":
                self.state = SessionRuntimeState.FAILED
            raise
