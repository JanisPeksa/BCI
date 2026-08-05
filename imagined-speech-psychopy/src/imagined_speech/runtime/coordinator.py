"""Session lifecycle and operator-command orchestration."""

from __future__ import annotations

import time
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path

from imagined_speech import __version__
from imagined_speech.acquisition import (
    AcquisitionRecorder,
    AcquisitionSnapshot,
    create_acquisition_backend,
)
from imagined_speech.config import ResolvedExperiment
from imagined_speech.runtime.clock import ProtocolClock, RealClock
from imagined_speech.runtime.protocol import (
    ProtocolEngine,
    FrameLockedProtocolEngine,
    RunState,
    TERMINAL_STATES,
)
from imagined_speech.events import CompositeEventSink, EventSource, MemoryEventSink
from imagined_speech.runtime.commands import (
    OperatorCommand,
    OperatorCommandRecord,
    OperatorCommandStatus,
)
from imagined_speech.ipc.messages import (
    FrameAcknowledgementPayload,
    FrameTimingPayload,
    TimingPreflightPayload,
)
from imagined_speech.planning import SessionPlan, compile_session_plan
from imagined_speech.recording import SessionValidationReport, SessionWriter, validate_session


class SessionRuntimeState(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    READY = "ready"
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
        frame_locked: bool = False,
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
        engine_type = FrameLockedProtocolEngine if frame_locked else ProtocolEngine
        self.engine = engine_type(
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
        self._protocol_started = False
        self.frame_locked = frame_locked
        self.clock_offset_ns = 0
        self.preflight_passed = not frame_locked
        self.final_clock_calibrated = not frame_locked
        self._frame_intervals: list[float] = []
        self._neutral_started_ns: int | None = None
        self.timing_warnings: list[str] = []
        self._dropped_frame_count = 0
        self._unattributed_dropped_frame_count = 0
        self._affected_trial_attempts: dict[
            tuple[str, int], dict[str, object]
        ] = {}
        self._dropped_frame_warning: str | None = None

    @property
    def needs_final_clock_calibration(self) -> bool:
        return bool(
            self.frame_locked
            and not self.final_clock_calibrated
            and self.state == SessionRuntimeState.POST_ROLL
            and self._roll_deadline is not None
            and self.clock.monotonic() >= self._roll_deadline
        )

    def record_clock_calibration(
        self,
        *,
        phase: str,
        offset_ns: int,
        round_trip_ns: int,
        samples: list[dict[str, int]],
    ) -> None:
        self.clock_offset_ns = offset_ns
        self.writer.record_presentation_metadata({
            f"clock_calibration_{phase}": {
                "offset_ns": offset_ns,
                "round_trip_ns": round_trip_ns,
                "samples": samples,
            }
        })
        if phase == "final":
            self.final_clock_calibrated = True

    @property
    def session_path(self) -> Path:
        return self.writer.path

    @property
    def recording(self) -> bool:
        return self.acquisition.running

    @property
    def protocol_started(self) -> bool:
        return self._protocol_started

    def start(self) -> None:
        if self.state != SessionRuntimeState.CREATED:
            raise RuntimeError(f"cannot start runtime from {self.state.value}")
        self.state = SessionRuntimeState.CONNECTING
        try:
            self.acquisition.start()
            self.state = SessionRuntimeState.READY
        except Exception as exc:
            self.error = str(exc)
            self.engine.fail(str(exc))
            self._finalize("failed")
            self.state = SessionRuntimeState.FAILED
            raise

    def record_timing_preflight(self, result: TimingPreflightPayload) -> None:
        if result.frame_intervals_seconds:
            self.writer.record_preflight_frame_intervals(
                result.frame_intervals_seconds
            )
        expected = 1 / self.resolved.config.presentation.psychopy.refresh_rate_hz
        maximum = (
            expected
            * self.resolved.config.presentation.psychopy.max_frame_interval_factor
        )
        dropped_intervals = tuple(
            value for value in result.frame_intervals_seconds if value > maximum
        )
        dropped_count = len(dropped_intervals)
        quality_warning = result.error is None and (
            not result.passed or dropped_count > 0
        )
        self.preflight_passed = result.error is None
        if quality_warning:
            warning = (
                f"Display timing preflight warning: {dropped_count} dropped frame(s); "
                "the protocol may continue and timing details were logged."
                if dropped_count
                else (
                    "Display timing preflight warning: configured timing thresholds "
                    "were not met. The protocol may continue; timing details were logged."
                )
            )
            self.timing_warnings.append(warning)
        outcome = (
            "failed"
            if result.error is not None
            else "warning" if quality_warning else "passed"
        )
        self.writer.record_presentation_metadata({
            "driver": "psychopy",
            "software_version": __version__,
            "passed": result.passed,
            "threshold_outcome": outcome,
            "timing_policy": "warn_and_continue",
            "timing_warnings": list(self.timing_warnings),
            "measured_refresh_rate_hz": result.measured_refresh_rate_hz,
            "dropped_frame_fraction": result.dropped_frame_fraction,
            "frame_interval_count": result.frame_interval_count,
            "psychopy": self.resolved.config.presentation.psychopy.model_dump(mode="json"),
            "audio": self.resolved.config.presentation.audio.model_dump(mode="json"),
            "details": result.metadata,
            "error": result.error,
            "preflight_timing_quality": {
                "frame_interval_count": len(result.frame_intervals_seconds),
                "dropped_frame_count": dropped_count,
                "dropped_frame_fraction": result.dropped_frame_fraction,
                "maximum_frame_interval_seconds": maximum,
                "max_observed_frame_interval_seconds": (
                    max(result.frame_intervals_seconds)
                    if result.frame_intervals_seconds
                    else None
                ),
                "artifact": (
                    "preflight-frame-intervals.csv"
                    if result.frame_intervals_seconds
                    else None
                ),
            },
        })
        # Timing-quality misses are evidence to preserve, not a reason to discard
        # an otherwise usable recording. Errors mean the subject UI or clock
        # calibration did not initialize and remain fatal.
        if not self.preflight_passed:
            reason = result.error or "display timing preflight failed"
            self.error = reason
            self.engine.fail(reason)
            self._finalize("failed")
            self.state = SessionRuntimeState.FAILED

    def _ingest_frame_intervals(
        self,
        *,
        intervals: tuple[float, ...],
        engine_before: dict[str, object],
        presentation_id: str | None,
        record: dict[str, object],
    ) -> dict[str, object]:
        self._frame_intervals.extend(intervals)
        self.writer.record_frame_intervals(self._frame_intervals)
        expected = 1 / self.resolved.config.presentation.psychopy.refresh_rate_hz
        maximum_interval = (
            expected
            * self.resolved.config.presentation.psychopy.max_frame_interval_factor
        )
        dropped_intervals = tuple(
            value for value in intervals if value > maximum_interval
        )
        dropped_count = len(dropped_intervals)
        self._dropped_frame_count += dropped_count
        trial_id = engine_before.get("trial_id")
        attempt_value = engine_before.get("attempt")
        attempt = int(attempt_value) if isinstance(attempt_value, int) else 1
        trial_number_value = engine_before.get("trial_number")
        trial_number = (
            int(trial_number_value) if isinstance(trial_number_value, int) else None
        )

        if dropped_count and isinstance(trial_id, str):
            key = (trial_id, attempt)
            affected = self._affected_trial_attempts.setdefault(key, {
                "trial_id": trial_id,
                "trial_number": trial_number,
                "attempt": attempt,
                "block_id": engine_before.get("block_id"),
                "dropped_frame_count": 0,
                "max_frame_interval_seconds": 0.0,
                "presentation_ids": [],
                "step_ids": [],
                "screens": [],
            })
            affected["dropped_frame_count"] = (
                int(affected["dropped_frame_count"]) + dropped_count
            )
            affected["max_frame_interval_seconds"] = max(
                float(affected["max_frame_interval_seconds"]),
                max(dropped_intervals),
            )
            for field, value in (
                ("presentation_ids", presentation_id),
                ("step_ids", engine_before.get("current_step_id")),
                ("screens", engine_before.get("current_screen")),
            ):
                values = affected[field]
                if isinstance(values, list) and value is not None and value not in values:
                    values.append(value)
        elif dropped_count:
            self._unattributed_dropped_frame_count += dropped_count

        affected_trials = sorted(
            self._affected_trial_attempts.values(),
            key=lambda value: (
                int(value["trial_number"])
                if isinstance(value["trial_number"], int)
                else 0,
                str(value["trial_id"]),
                int(value["attempt"]),
            ),
        )
        fraction = self._dropped_frame_count / len(self._frame_intervals)
        minimum_sample_count = (
            self.resolved.config.presentation.psychopy.preflight_frame_count
        )
        threshold_exceeded = (
            len(self._frame_intervals) >= minimum_sample_count
            and fraction
            > self.resolved.config.presentation.psychopy.max_dropped_frame_fraction
        )
        summary: dict[str, object] = {
            "policy": "warn_and_continue",
            "frame_interval_count": len(self._frame_intervals),
            "dropped_frame_count": self._dropped_frame_count,
            "dropped_frame_fraction": fraction,
            "maximum_frame_interval_seconds": maximum_interval,
            "configured_max_dropped_frame_fraction": (
                self.resolved.config.presentation.psychopy.max_dropped_frame_fraction
            ),
            "threshold_exceeded": threshold_exceeded,
            "affected_trial_attempts": affected_trials,
            "unattributed_dropped_frame_count": self._unattributed_dropped_frame_count,
        }

        record["active_frame_interval_count"] = len(self._frame_intervals)
        record["active_dropped_frame_count"] = self._dropped_frame_count
        record["active_dropped_frame_fraction"] = fraction
        record["active_timing_threshold_exceeded"] = threshold_exceeded
        record["timing_quality_summary"] = summary
        metadata: dict[str, object] = {"active_timing_quality": summary}

        if dropped_count:
            if self._dropped_frame_warning in self.timing_warnings:
                self.timing_warnings.remove(self._dropped_frame_warning)
            latest = "outside a trial"
            if isinstance(trial_id, str):
                trial_label = (
                    f"trial {trial_number}" if trial_number is not None else trial_id
                )
                latest = f"{trial_label}, attempt {attempt}"
            trial_attempt_count = len(affected_trials)
            self._dropped_frame_warning = (
                f"Display timing warning: {self._dropped_frame_count} dropped frame(s) "
                f"affected {trial_attempt_count} trial attempt(s) (latest: {latest}). "
                "The protocol is continuing; pause if display instability persists."
            )
            self.timing_warnings.append(self._dropped_frame_warning)
            record["timing_warning"] = self._dropped_frame_warning
            record["dropped_frame_intervals_seconds"] = dropped_intervals
            metadata.update({
                "threshold_outcome": "warning",
                "timing_policy": "warn_and_continue",
                "timing_warnings": list(self.timing_warnings),
            })
        self.writer.record_presentation_metadata(metadata)
        return summary

    def presentation_state(self) -> dict[str, object] | None:
        if not isinstance(self.engine, FrameLockedProtocolEngine):
            return None
        if self.engine.state in {RunState.READY, RunState.PAUSED, *TERMINAL_STATES}:
            return None
        current = asdict(self.engine.view_state())
        current["run_state"] = self.engine.view_state().run_state.value
        successor_value = self.engine.successor_view_state()
        successor = asdict(successor_value) if successor_value is not None else None
        if successor is not None:
            successor["run_state"] = successor_value.run_state.value
        return {
            "revision": self.engine.presentation_revision,
            "interrupt": self.engine.awaiting_neutral,
            "current": current,
            "successor": successor,
        }

    def acknowledge_frame(
        self,
        acknowledgement: FrameAcknowledgementPayload,
        *,
        revision: int,
    ) -> None:
        if not isinstance(self.engine, FrameLockedProtocolEngine):
            raise RuntimeError("runtime is not using frame-locked presentation")
        received_ns = time.perf_counter_ns()
        occurrence_ns = acknowledgement.subject_monotonic_ns + self.clock_offset_ns
        occurrence_seconds = occurrence_ns / 1_000_000_000
        engine_before = self.engine.diagnostic_state()
        record = {
            "kind": "frame_acknowledgement",
            "accepted": False,
            "revision": revision,
            "previous_presentation_id": acknowledgement.previous_presentation_id,
            "presentation_id": acknowledgement.presentation_id,
            "neutral": acknowledgement.neutral,
            "flip_time": acknowledgement.flip_time,
            "subject_monotonic_ns": acknowledgement.subject_monotonic_ns,
            "backend_occurrence_monotonic_ns": occurrence_ns,
            "backend_receive_monotonic_ns": received_ns,
            "transport_delay_ns": max(0, received_ns - occurrence_ns),
            "wall_time_utc": acknowledgement.wall_time_utc.isoformat(),
            "frame_index": acknowledgement.frame_index,
            "dropped_frames": acknowledgement.dropped_frames,
            "frame_interval_seconds": acknowledgement.frame_interval_seconds,
            "frame_interval_count": len(acknowledgement.frame_intervals_seconds),
            "frame_intervals_seconds": acknowledgement.frame_intervals_seconds,
            "audio_scheduled_time": acknowledgement.audio_scheduled_time,
            "audio_started": acknowledgement.audio_started,
            "engine_before": engine_before,
            "expected_revision": self.engine.presentation_revision,
            "expected_previous_presentation_id": engine_before.get(
                "expected_neutral_previous_presentation_id"
            ) or engine_before.get("presentation_id"),
        }
        try:
            self.engine.acknowledge_frame(
                previous_presentation_id=acknowledgement.previous_presentation_id,
                presentation_id=acknowledgement.presentation_id,
                revision=revision,
                monotonic_seconds=occurrence_seconds,
                wall_time_utc=acknowledgement.wall_time_utc,
                neutral=acknowledgement.neutral,
            )
            marker_attempt_ns = time.perf_counter_ns()
            record["marker_attempt_backend_monotonic_ns"] = marker_attempt_ns
            record["marker_latency_ns"] = max(0, marker_attempt_ns - occurrence_ns)
            record["accepted"] = True
            record["engine_after"] = self.engine.diagnostic_state()
            if acknowledgement.neutral:
                self._neutral_started_ns = occurrence_ns
                record["neutral_gap_started"] = True
            elif self._neutral_started_ns is not None:
                record["neutral_gap_duration_ns"] = max(
                    0, occurrence_ns - self._neutral_started_ns
                )
                self._neutral_started_ns = None
            intervals = acknowledgement.frame_intervals_seconds
            if not intervals and acknowledgement.frame_interval_seconds is not None:
                intervals = (acknowledgement.frame_interval_seconds,)
            if intervals:
                presentation_id = (
                    acknowledgement.previous_presentation_id
                    or (
                        str(engine_before["presentation_id"])
                        if engine_before.get("presentation_id") is not None
                        else acknowledgement.presentation_id
                    )
                )
                self._ingest_frame_intervals(
                    intervals=intervals,
                    engine_before=engine_before,
                    presentation_id=presentation_id,
                    record=record,
                )
        except Exception as exc:
            record["error"] = str(exc)
            record["exception_type"] = type(exc).__name__
            record["engine_after"] = self.engine.diagnostic_state()
            raise
        finally:
            self.writer.record_presentation_timing(record)

    def record_frame_timing(
        self,
        timing: FrameTimingPayload,
        *,
        revision: int,
    ) -> None:
        if not isinstance(self.engine, FrameLockedProtocolEngine):
            raise RuntimeError("runtime is not using frame-locked presentation")
        received_ns = time.perf_counter_ns()
        occurrence_ns = timing.subject_monotonic_ns + self.clock_offset_ns
        engine_before = self.engine.diagnostic_state()
        record: dict[str, object] = {
            "kind": "frame_timing_sample",
            "accepted": False,
            "revision": revision,
            "presentation_id": timing.presentation_id,
            "subject_monotonic_ns": timing.subject_monotonic_ns,
            "backend_occurrence_monotonic_ns": occurrence_ns,
            "backend_receive_monotonic_ns": received_ns,
            "transport_delay_ns": max(0, received_ns - occurrence_ns),
            "wall_time_utc": timing.wall_time_utc.isoformat(),
            "frame_index": timing.frame_index,
            "dropped_frames": timing.dropped_frames,
            "frame_interval_count": len(timing.frame_intervals_seconds),
            "frame_intervals_seconds": timing.frame_intervals_seconds,
            "engine_before": engine_before,
        }
        try:
            if revision != self.engine.presentation_revision:
                raise ValueError("stale frame timing revision")
            if timing.presentation_id != self.engine.presentation_id:
                raise ValueError("frame timing does not match active presentation")
            if self.engine.state in TERMINAL_STATES:
                raise ValueError("frame timing arrived after protocol termination")
            record["accepted"] = True
            self._ingest_frame_intervals(
                intervals=timing.frame_intervals_seconds,
                engine_before=engine_before,
                presentation_id=timing.presentation_id,
                record=record,
            )
        except Exception as exc:
            record["error"] = str(exc)
            record["exception_type"] = type(exc).__name__
            raise
        finally:
            self.writer.record_presentation_timing(record)

    def confirm_subject_abort(
        self,
        acknowledgement: FrameAcknowledgementPayload,
        *,
        revision: int,
        reason: str,
    ) -> None:
        if not isinstance(self.engine, FrameLockedProtocolEngine):
            raise RuntimeError("runtime is not using frame-locked presentation")
        if revision != self.engine.presentation_revision:
            raise ValueError("stale subject abort revision")
        if not acknowledgement.neutral or acknowledgement.presentation_id is not None:
            raise ValueError("subject abort must confirm a neutral frame")
        if acknowledgement.previous_presentation_id != self.engine.presentation_id:
            raise ValueError("subject abort does not match the active presentation")
        received_ns = time.perf_counter_ns()
        occurrence_ns = acknowledgement.subject_monotonic_ns + self.clock_offset_ns
        engine_before = self.engine.diagnostic_state()
        record: dict[str, object] = {
            "kind": "subject_abort",
            "accepted": False,
            "reason": reason,
            "revision": revision,
            "previous_presentation_id": acknowledgement.previous_presentation_id,
            "subject_monotonic_ns": acknowledgement.subject_monotonic_ns,
            "backend_occurrence_monotonic_ns": occurrence_ns,
            "backend_receive_monotonic_ns": received_ns,
            "transport_delay_ns": max(0, received_ns - occurrence_ns),
            "wall_time_utc": acknowledgement.wall_time_utc.isoformat(),
            "flip_time": acknowledgement.flip_time,
            "frame_index": acknowledgement.frame_index,
            "dropped_frames": acknowledgement.dropped_frames,
            "frame_interval_seconds": acknowledgement.frame_interval_seconds,
            "frame_interval_count": len(acknowledgement.frame_intervals_seconds),
            "frame_intervals_seconds": acknowledgement.frame_intervals_seconds,
            "engine_before": engine_before,
        }
        try:
            self.engine.confirm_subject_abort(
                monotonic_seconds=occurrence_ns / 1_000_000_000,
                wall_time_utc=acknowledgement.wall_time_utc,
            )
            marker_attempt_ns = time.perf_counter_ns()
            record["marker_attempt_backend_monotonic_ns"] = marker_attempt_ns
            record["marker_latency_ns"] = max(0, marker_attempt_ns - occurrence_ns)
            record["accepted"] = True
            intervals = acknowledgement.frame_intervals_seconds
            if not intervals and acknowledgement.frame_interval_seconds is not None:
                intervals = (acknowledgement.frame_interval_seconds,)
            if intervals:
                self._ingest_frame_intervals(
                    intervals=intervals,
                    engine_before=engine_before,
                    presentation_id=acknowledgement.previous_presentation_id,
                    record=record,
                )
        except Exception as exc:
            record["error"] = str(exc)
            raise
        finally:
            self.writer.record_presentation_timing(record)

    def start_protocol(self) -> None:
        """Begin configured pre-roll exactly once after recording is active."""
        if self.state != SessionRuntimeState.READY:
            raise RuntimeError(f"cannot start protocol from {self.state.value}")
        if self._protocol_started:
            raise RuntimeError("protocol has already been started")
        self._protocol_started = True
        self.state = SessionRuntimeState.PRE_ROLL
        self._roll_deadline = (
            self.clock.monotonic() + self.resolved.device.pre_roll_seconds
        )

    def tick(self) -> None:
        if (
            self.state in {
                SessionRuntimeState.CREATED,
                SessionRuntimeState.CONNECTING,
                SessionRuntimeState.READY,
                SessionRuntimeState.PRE_ROLL,
            }
            and self.engine.state in TERMINAL_STATES
        ):
            self.state = SessionRuntimeState.POST_ROLL
            self._roll_deadline = (
                self.clock.monotonic() + self.resolved.device.post_roll_seconds
            )
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
                if self.frame_locked and not self.final_clock_calibrated:
                    return
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
        runtime_before = self.state
        before = self.engine.state
        engine_before = self.engine.diagnostic_state()
        context = self.engine.current_action.context if self.engine.current_action else None
        status = OperatorCommandStatus.ACCEPTED
        reason = f"{command.value} accepted"
        command_attempt = len(self.operator_records) + 1
        self.writer.record_presentation_timing({
            "kind": "operator_command_requested",
            "command_attempt": command_attempt,
            "command": command.value,
            "source": source,
            "note": note,
            "runtime_state": self.state.value,
            "engine": engine_before,
            "backend_monotonic_ns": time.perf_counter_ns(),
        })
        try:
            event_source = (
                EventSource.SYSTEM if source == EventSource.SYSTEM.value
                else EventSource.EXPERIMENTER_UI
            )
            if command == OperatorCommand.START_PROTOCOL:
                self.start_protocol()
            elif command == OperatorCommand.PAUSE:
                self.engine.pause(event_source)
            elif command == OperatorCommand.RESUME:
                self.engine.resume(event_source)
            elif command == OperatorCommand.REPEAT_TRIAL:
                self.engine.repeat_current_trial(event_source)
            elif command == OperatorCommand.REPEAT_BLOCK:
                self.engine.repeat_current_block(event_source)
            elif command == OperatorCommand.START_EXPERIMENT:
                self.engine.start_experiment(event_source)
            elif command == OperatorCommand.REPEAT_LAST_PRACTICE_TRIAL:
                self.engine.repeat_last_practice_trial(event_source)
            elif command == OperatorCommand.REFIT:
                self.engine.record_refit(note or "", event_source)
            elif command == OperatorCommand.ABORT:
                self.engine.abort(event_source)
            else:  # pragma: no cover - exhaustive StrEnum guard
                raise ValueError(f"unsupported operator command: {command}")
        except (RuntimeError, ValueError) as exc:
            status = OperatorCommandStatus.REJECTED
            reason = str(exc)

        engine_after = self.engine.diagnostic_state()
        if status == OperatorCommandStatus.ACCEPTED:
            pending_control = engine_after.get("pending_control")
            if pending_control:
                reason = (
                    f"{command.value} accepted; queued {pending_control} and awaiting "
                    "neutral frame acknowledgement"
                )
            elif self.engine.state == RunState.AWAITING_PRESENTATION:
                reason = (
                    f"{command.value} accepted; awaiting presentation onset "
                    "acknowledgement"
                )
            elif (
                command in {OperatorCommand.REPEAT_TRIAL, OperatorCommand.REPEAT_BLOCK}
                and before == RunState.PAUSED
                and self.engine.state == RunState.PAUSED
            ):
                reason = (
                    f"{command.value} accepted; repeated scope prepared while neutral; "
                    "protocol remains paused until resume"
                )
            elif command == OperatorCommand.START_PROTOCOL:
                reason = (
                    "start_protocol accepted; acquisition pre-roll started; protocol "
                    "will await its first presentation after pre-roll"
                )
            elif command == OperatorCommand.START_EXPERIMENT:
                reason = "start_experiment accepted; experiment block 1 is starting"
            elif command == OperatorCommand.REPEAT_LAST_PRACTICE_TRIAL:
                reason = (
                    "repeat_last_practice_trial accepted; final practice trial retry "
                    "is starting"
                )

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
            stage_type=context.stage_type if context else None,
            block_id=context.block_id if context else None,
            trial_id=context.trial_id if context else None,
            attempt=context.attempt if context else None,
            payload={
                "runtime_state": self.state.value,
                "runtime_state_before": runtime_before.value,
                "runtime_state_after": self.state.value,
                "engine_before": engine_before,
                "engine_after": engine_after,
            },
        )
        self.operator_records.append(record)
        self.writer.record_presentation_timing({
            "kind": "operator_command_result",
            "command_sequence": record.sequence_number,
            "command": command.value,
            "status": status.value,
            "reason": reason,
            "runtime_state_before": runtime_before.value,
            "runtime_state_after": self.state.value,
            "engine_before": engine_before,
            "engine_after": engine_after,
            "backend_monotonic_ns": time.perf_counter_ns(),
        })
        return record

    def acquisition_snapshot(self, max_samples: int = 750) -> AcquisitionSnapshot:
        return self.acquisition.snapshot(max_samples)

    def close(self) -> None:
        if self._finalized:
            return
        if self.engine.state not in TERMINAL_STATES:
            self.engine.fail("application shutdown before controlled finalization completed")
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
