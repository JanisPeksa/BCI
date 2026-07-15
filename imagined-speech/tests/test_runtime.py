import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.engine import ProtocolEngine, RunState, VirtualClock
from imagined_speech.events import EventType
from imagined_speech.operator import OperatorCommand, OperatorCommandStatus
from imagined_speech.plan import compile_session_plan
from imagined_speech.runtime import SessionRuntime, SessionRuntimeState
from imagined_speech.session import SessionWriter, validate_session


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
