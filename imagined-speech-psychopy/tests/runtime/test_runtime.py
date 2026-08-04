import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.runtime.clock import VirtualClock
from imagined_speech.runtime.protocol import (
    FrameLockedProtocolEngine,
    ProtocolEngine,
    RunState,
)
from imagined_speech.events import EventType
from imagined_speech.runtime.commands import OperatorCommand, OperatorCommandStatus
from imagined_speech.planning import compile_session_plan
from imagined_speech.runtime.coordinator import SessionRuntime, SessionRuntimeState
from imagined_speech.recording import SessionWriter, validate_session
from imagined_speech.ipc.messages import FrameAcknowledgementPayload


def test_runtime_records_accepted_and_rejected_commands(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(
        resolved,
        "OP001",
        output_root=tmp_path,
        session_label="CONTROL",
        clock=clock,
    )
    runtime.start()
    assert runtime.state == SessionRuntimeState.READY
    runtime.start_protocol()
    clock.advance(resolved.device.pre_roll_seconds)
    runtime.acquisition.capture_available()
    runtime.tick()

    accepted = runtime.execute(OperatorCommand.PAUSE)
    rejected = runtime.execute(OperatorCommand.PAUSE)
    refit = runtime.execute(OperatorCommand.REFIT, note="Adjusted F3 contact")
    resumed = runtime.execute(OperatorCommand.RESUME)
    aborted = runtime.execute(OperatorCommand.ABORT)

    assert accepted.status == OperatorCommandStatus.ACCEPTED
    assert rejected.status == OperatorCommandStatus.REJECTED
    assert refit.status == OperatorCommandStatus.ACCEPTED
    assert resumed.status == OperatorCommandStatus.ACCEPTED
    assert aborted.status == OperatorCommandStatus.ACCEPTED
    assert runtime.engine.state == RunState.ABORTED
    assert accepted.payload["engine_before"]["engine_state"] == "running"
    assert accepted.payload["engine_after"]["engine_state"] == "paused"

    runtime.close()
    assert runtime.state == SessionRuntimeState.FINALIZED
    report = validate_session(runtime.session_path)
    assert report.status == "aborted"
    assert report.operator_command_count == 5
    assert report.sample_count > 0
    manifest = json.loads(
        (runtime.session_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["session_label"] == "CONTROL"
    assert EventType.REFIT_RECORDED in [
        event.event_type for event in runtime.event_memory.events
    ]
    timing_kinds = [
        json.loads(line)["kind"]
        for line in (runtime.session_path / "presentation-timing.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert "operator_command_requested" in timing_kinds
    assert "operator_command_result" in timing_kinds


def test_repeated_trial_session_reconstructs_as_complete(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(resolved, plan, "REPEAT001", tmp_path)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    engine = ProtocolEngine(writer.session_id, plan, resolved.config, clock, writer)
    engine.start()
    clock.advance(engine.remaining_seconds)
    engine.tick()
    engine.repeat_current_trial()
    while engine.state == RunState.RUNNING:
        clock.advance(engine.remaining_seconds)
        engine.tick()

    report = validate_session(writer.path)
    assert report.status == "complete"
    assert report.trial_count == 3
    assert report.phase_count == 12


def test_repeated_block_session_reconstructs_as_complete(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(resolved, plan, "BLOCK001", tmp_path)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    engine = ProtocolEngine(writer.session_id, plan, resolved.config, clock, writer)
    engine.start()
    while not (
        engine.current_action is not None
        and engine.current_action.context.block_type == "experiment"
    ):
        clock.advance(engine.remaining_seconds)
        engine.tick()
    engine.repeat_current_block()
    while engine.state == RunState.RUNNING:
        clock.advance(engine.remaining_seconds)
        engine.tick()

    report = validate_session(writer.path)
    assert report.status == "complete"
    assert report.trial_count == 3
    assert report.phase_count == 12


def test_acquisition_snapshot_exposes_recent_raw_copy(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(resolved, "TRACE001", output_root=tmp_path, clock=clock)
    runtime.start()
    clock.advance(1)
    runtime.acquisition.capture_available()
    runtime.acquisition.flush_pending()

    snapshot = runtime.acquisition_snapshot(max_samples=50)

    assert snapshot.sample_count == 250
    assert len(snapshot.recent_samples) == 50
    assert snapshot.channel_names[-1] == "marker"
    runtime.close()


def test_recording_waits_for_explicit_protocol_start(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(resolved, "READY001", output_root=tmp_path, clock=clock)
    runtime.start()
    clock.advance(5)
    runtime.acquisition.capture_available()
    runtime.acquisition.flush_pending()
    runtime.tick()

    assert runtime.state == SessionRuntimeState.READY
    assert runtime.engine.state == RunState.READY
    assert runtime.acquisition_snapshot().sample_count > 0

    accepted = runtime.execute(OperatorCommand.START_PROTOCOL)
    rejected = runtime.execute(OperatorCommand.START_PROTOCOL)
    assert accepted.status == OperatorCommandStatus.ACCEPTED
    assert rejected.status == OperatorCommandStatus.REJECTED
    assert runtime.state == SessionRuntimeState.PRE_ROLL
    runtime.close()


def test_abort_before_protocol_start_is_valid_and_explicit(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(resolved, "READYABORT", output_root=tmp_path, clock=clock)
    runtime.start()
    clock.advance(1)
    runtime.acquisition.capture_available()
    runtime.acquisition.flush_pending()

    record = runtime.execute(OperatorCommand.ABORT)
    runtime.tick()
    clock.advance(resolved.device.post_roll_seconds)
    runtime.tick()

    assert record.status == OperatorCommandStatus.ACCEPTED
    assert not runtime.protocol_started
    assert runtime.state == SessionRuntimeState.FINALIZED
    assert validate_session(runtime.session_path).status == "aborted"
    event = runtime.event_memory.events[-1]
    assert event.event_type == EventType.SESSION_ABORTED
    assert event.payload["protocol_started"] is False


def test_active_timing_uses_all_frames_not_only_boundary_frames(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(
        resolved,
        "TIMING001",
        output_root=tmp_path,
        clock=clock,
        frame_locked=True,
    )
    runtime.engine.start()
    first = runtime.engine.presentation_id
    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            presentation_id=first,
            subject_monotonic_ns=1_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, tzinfo=UTC),
            frame_index=1,
        ),
        revision=runtime.engine.presentation_revision,
    )
    successor = runtime.engine.successor_view_state()
    assert successor is not None
    intervals = (0.02851,) + (1 / 60,) * 119

    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            previous_presentation_id=first,
            presentation_id=successor.presentation_id,
            subject_monotonic_ns=3_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
            frame_index=121,
            dropped_frames=1,
            frame_interval_seconds=intervals[-1],
            frame_intervals_seconds=intervals,
        ),
        revision=runtime.engine.presentation_revision,
    )

    assert runtime.engine.state == RunState.RUNNING
    assert runtime.error is None
    rows = (runtime.session_path / "frame-intervals.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(rows) == 121


def test_frame_locked_pause_repeat_trial_and_block_session_validates(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(resolved, plan, "RECOVERY001", tmp_path)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    engine = FrameLockedProtocolEngine(
        writer.session_id,
        plan,
        resolved.config,
        clock,
        writer,
    )

    def acknowledge_onset() -> None:
        clock.advance(0.01)
        engine.acknowledge_frame(
            previous_presentation_id=None,
            presentation_id=engine.presentation_id,
            revision=engine.presentation_revision,
            monotonic_seconds=clock.monotonic(),
            wall_time_utc=clock.wall_time_utc(),
            neutral=False,
        )

    def acknowledge_boundary() -> None:
        assert engine.current_action is not None
        previous = engine.presentation_id
        successor = engine.successor_view_state()
        clock.advance(engine.current_action.duration_seconds)
        engine.acknowledge_frame(
            previous_presentation_id=previous,
            presentation_id=(successor.presentation_id if successor else None),
            revision=engine.presentation_revision,
            monotonic_seconds=clock.monotonic(),
            wall_time_utc=clock.wall_time_utc(),
            neutral=successor is None,
        )

    def pause() -> None:
        active = engine.presentation_id
        engine.pause()
        clock.advance(0.01)
        engine.acknowledge_frame(
            previous_presentation_id=active,
            presentation_id=None,
            revision=engine.presentation_revision,
            monotonic_seconds=clock.monotonic(),
            wall_time_utc=clock.wall_time_utc(),
            neutral=True,
        )

    def resume() -> None:
        engine.resume()
        acknowledge_onset()

    def pause_and_repeat(command: str) -> None:
        pause()
        getattr(engine, command)()
        assert engine.state == RunState.PAUSED
        resume()

    engine.start()
    acknowledge_onset()
    while not (
        engine.current_action is not None
        and engine.current_action.context.block_type == "practice"
        and engine.current_action.context.phase is not None
        and engine.current_action.context.phase.value == "thinking"
    ):
        acknowledge_boundary()
    pause_and_repeat("repeat_current_trial")

    while not (
        engine.current_action is not None
        and engine.current_action.context.block_type == "experiment"
        and engine.current_action.context.phase is not None
        and engine.current_action.context.phase.value == "stimulus"
        and engine.current_action.context.attempt == 1
    ):
        acknowledge_boundary()
    pause_and_repeat("repeat_current_trial")

    while not (
        engine.current_action is not None
        and engine.current_action.context.block_type == "experiment"
        and engine.current_action.context.phase is not None
        and engine.current_action.context.phase.value == "thinking"
        and engine.current_action.context.attempt == 2
    ):
        acknowledge_boundary()
    pause()
    engine.repeat_current_trial()
    assert engine.current_action is not None
    assert engine.current_action.context.attempt == 3
    engine.repeat_current_block()
    assert engine.current_action is not None
    assert engine.current_action.context.attempt == 3
    resume()

    while engine.state == RunState.RUNNING:
        acknowledge_boundary()

    report = validate_session(writer.path)
    events = [
        json.loads(line)
        for line in (writer.path / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert engine.state == RunState.COMPLETED
    assert report.status == "complete"
    assert report.phase_count == 12
    attempt_3_events = [
        event["event_type"]
        for event in events
        if event.get("trial_id") == "experiment-block-001-trial-001"
        and event.get("attempt") == 3
        and event["event_type"]
        in {"trial_started", "phase_started", "phase_ended", "trial_ended"}
    ]
    assert attempt_3_events[:2] == ["trial_started", "phase_started"]
