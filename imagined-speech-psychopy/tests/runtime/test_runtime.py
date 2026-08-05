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
from imagined_speech.ipc.messages import (
    FrameAcknowledgementPayload,
    FrameTimingPayload,
    TimingPreflightPayload,
)


def _drive_protocol(engine: ProtocolEngine, clock: VirtualClock) -> None:
    while engine.state not in {
        RunState.COMPLETED,
        RunState.ABORTED,
        RunState.FAILED,
    }:
        if engine.state == RunState.AWAITING_EXPERIMENT:
            engine.start_experiment()
        else:
            clock.advance(engine.remaining_seconds)
            engine.tick()


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
    _drive_protocol(engine, clock)

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
    while engine.state != RunState.AWAITING_EXPERIMENT:
        clock.advance(engine.remaining_seconds)
        engine.tick()
    engine.start_experiment()
    while not (
        engine.current_action is not None
        and engine.current_action.context.stage_type == "experiment"
    ):
        clock.advance(engine.remaining_seconds)
        engine.tick()
    engine.repeat_current_block()
    _drive_protocol(engine, clock)

    report = validate_session(writer.path)
    assert report.status == "complete"
    assert report.trial_count == 3
    assert report.phase_count == 12


def test_checkpoint_retry_session_reconstructs_as_complete(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(resolved, plan, "PRACTICE-RETRY", tmp_path)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    engine = ProtocolEngine(writer.session_id, plan, resolved.config, clock, writer)
    engine.start()
    while engine.state == RunState.RUNNING:
        clock.advance(engine.remaining_seconds)
        engine.tick()

    assert engine.state == RunState.AWAITING_EXPERIMENT
    engine.repeat_last_practice_trial()
    while engine.state == RunState.RUNNING:
        clock.advance(engine.remaining_seconds)
        engine.tick()
    engine.start_experiment()
    _drive_protocol(engine, clock)

    report = validate_session(writer.path)
    assert report.status == "complete"
    assert report.trial_count == plan.total_trial_count
    events = [
        json.loads(line)
        for line in (writer.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event["event_type"] == "practice_started" for event in events) == 2
    assert sum(event["event_type"] == "practice_ended" for event in events) == 2


def test_acquisition_remains_active_at_practice_checkpoint(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(
        resolved,
        "PRACTICE-CHECKPOINT",
        output_root=tmp_path,
        clock=clock,
    )
    runtime.start()
    runtime.start_protocol()
    clock.advance(resolved.device.pre_roll_seconds)
    runtime.tick()
    while runtime.engine.state == RunState.RUNNING:
        clock.advance(runtime.engine.remaining_seconds)
        runtime.tick()

    assert runtime.state == SessionRuntimeState.RUNNING
    assert runtime.engine.state == RunState.AWAITING_EXPERIMENT
    assert runtime.acquisition.running
    command = runtime.execute(OperatorCommand.START_EXPERIMENT)
    assert command.status == OperatorCommandStatus.ACCEPTED
    runtime.execute(OperatorCommand.ABORT)
    runtime.close()


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


def test_abort_during_subject_initialization_reaches_finalization(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    runtime = SessionRuntime(
        resolved,
        "EARLYABORT",
        output_root=tmp_path,
        clock=clock,
        frame_locked=True,
    )
    assert runtime.state == SessionRuntimeState.CREATED

    record = runtime.execute(OperatorCommand.ABORT)
    runtime.tick()

    assert record.status == OperatorCommandStatus.ACCEPTED
    assert runtime.engine.state == RunState.ABORTED
    assert runtime.state == SessionRuntimeState.POST_ROLL
    assert not runtime.protocol_started

    clock.advance(resolved.device.post_roll_seconds)
    runtime.tick()
    assert runtime.needs_final_clock_calibration

    # The backend performs this calibration with the connected subject before
    # asking the runtime to tick once more.
    runtime.final_clock_calibrated = True
    runtime.tick()

    assert runtime.state == SessionRuntimeState.FINALIZED
    assert validate_session(runtime.session_path).status == "aborted"
    assert not any(
        (runtime.session_path / name).exists()
        for name in runtime.acquisition.artifact_names
    )


def test_timing_preflight_threshold_miss_warns_without_failing_session(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    runtime = SessionRuntime(
        resolved,
        "PREFLIGHT",
        output_root=tmp_path,
        frame_locked=True,
    )

    runtime.record_timing_preflight(TimingPreflightPayload(
        passed=False,
        measured_refresh_rate_hz=59.9,
        dropped_frame_fraction=0.02,
        frame_interval_count=120,
        metadata={"refresh_ok": True, "drops_ok": False},
    ))

    assert runtime.preflight_passed
    assert runtime.state == SessionRuntimeState.CREATED
    assert runtime.engine.state == RunState.READY
    assert runtime.error is None
    assert "protocol may continue" in runtime.timing_warnings[0].lower()
    metadata = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["threshold_outcome"] == "warning"
    assert metadata["timing_policy"] == "warn_and_continue"


def test_preflight_drop_below_fraction_threshold_is_warned_and_persisted(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    runtime = SessionRuntime(
        resolved,
        "PREFLIGHTDROP",
        output_root=tmp_path,
        frame_locked=True,
    )
    intervals = (0.1172,) + (1 / 60,) * 119

    runtime.record_timing_preflight(TimingPreflightPayload(
        passed=True,
        measured_refresh_rate_hz=60.0,
        dropped_frame_fraction=1 / 120,
        frame_interval_count=120,
        frame_intervals_seconds=intervals,
        metadata={"refresh_ok": True, "drops_ok": True, "dropped_frames": 1},
    ))

    assert runtime.preflight_passed
    assert "1 dropped frame(s)" in runtime.timing_warnings[0]
    rows = (runtime.session_path / "preflight-frame-intervals.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(rows) == 121
    metadata = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["threshold_outcome"] == "warning"
    assert metadata["preflight_timing_quality"]["dropped_frame_count"] == 1
    assert metadata["preflight_timing_quality"]["artifact"] == (
        "preflight-frame-intervals.csv"
    )


def test_display_initialization_error_fails_and_persists_diagnostics(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    runtime = SessionRuntime(
        resolved,
        "DISPLAYFAIL",
        output_root=tmp_path,
        frame_locked=True,
    )
    display = {
        "requested": {"device_name": r"\\.\DISPLAY1"},
        "available": [{"device_name": r"\\.\DISPLAY2", "index": 0}],
        "verified": False,
    }

    runtime.record_timing_preflight(TimingPreflightPayload(
        passed=False,
        measured_refresh_rate_hz=0,
        dropped_frame_fraction=1,
        frame_interval_count=0,
        metadata={"display": display},
        error="requested subject display is unavailable",
    ))

    assert runtime.preflight_passed is False
    assert runtime.state == SessionRuntimeState.FAILED
    metadata = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["details"]["display"] == display


def test_active_timing_warns_and_marks_affected_trial_without_stopping(
    tmp_path: Path,
) -> None:
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
    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            previous_presentation_id=first,
            presentation_id=successor.presentation_id,
            subject_monotonic_ns=2_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            frame_index=1,
        ),
        revision=runtime.engine.presentation_revision,
    )
    active_trial_presentation = runtime.engine.presentation_id
    assert runtime.engine.diagnostic_state()["trial_id"] is not None
    successor = runtime.engine.successor_view_state()
    assert successor is not None
    intervals = (0.02851, 0.1172) + (1 / 60,) * 118

    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            previous_presentation_id=active_trial_presentation,
            presentation_id=successor.presentation_id,
            subject_monotonic_ns=3_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
            frame_index=121,
            dropped_frames=2,
            frame_interval_seconds=intervals[-1],
            frame_intervals_seconds=intervals,
        ),
        revision=runtime.engine.presentation_revision,
    )

    assert runtime.engine.state == RunState.RUNNING
    assert runtime.error is None
    assert len(runtime.timing_warnings) == 1
    assert "2 dropped frame(s)" in runtime.timing_warnings[0]
    rows = (runtime.session_path / "frame-intervals.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(rows) == 121
    metadata = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(encoding="utf-8")
    )
    quality = metadata["active_timing_quality"]
    assert quality["policy"] == "warn_and_continue"
    assert quality["dropped_frame_count"] == 2
    assert quality["threshold_exceeded"] is True
    assert quality["unattributed_dropped_frame_count"] == 0
    assert len(quality["affected_trial_attempts"]) == 1
    affected = quality["affected_trial_attempts"][0]
    assert affected["trial_id"]
    assert affected["attempt"] == 1
    assert affected["dropped_frame_count"] == 2
    assert affected["presentation_ids"] == [active_trial_presentation]

    clean_successor = runtime.engine.successor_view_state()
    assert clean_successor is not None
    clean_intervals = (1 / 60,) * 200
    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            previous_presentation_id=runtime.engine.presentation_id,
            presentation_id=clean_successor.presentation_id,
            subject_monotonic_ns=4_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 4, tzinfo=UTC),
            frame_index=321,
            dropped_frames=2,
            frame_interval_seconds=clean_intervals[-1],
            frame_intervals_seconds=clean_intervals,
        ),
        revision=runtime.engine.presentation_revision,
    )
    updated = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(encoding="utf-8")
    )["active_timing_quality"]
    assert updated["frame_interval_count"] == 320
    assert updated["dropped_frame_count"] == 2
    assert updated["dropped_frame_fraction"] == pytest.approx(2 / 320)
    assert updated["threshold_exceeded"] is False
    assert len(runtime.timing_warnings) == 1


def test_immediate_frame_timing_warns_without_advancing_protocol(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    runtime = SessionRuntime(
        resolved,
        "LIVETIMING",
        output_root=tmp_path,
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
    revision = runtime.engine.presentation_revision
    presentation_id = runtime.engine.presentation_id
    state_before = runtime.engine.diagnostic_state()

    runtime.record_frame_timing(
        FrameTimingPayload(
            presentation_id=presentation_id or "",
            subject_monotonic_ns=1_500_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=UTC),
            frame_index=20,
            dropped_frames=1,
            frame_intervals_seconds=((1 / 60,) * 18 + (0.1172,)),
        ),
        revision=revision,
    )

    assert runtime.engine.diagnostic_state() == state_before
    assert runtime.error is None
    assert "1 dropped frame(s)" in runtime.timing_warnings[0]
    records = [
        json.loads(line)
        for line in (runtime.session_path / "presentation-timing.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[-1]["kind"] == "frame_timing_sample"
    assert records[-1]["accepted"] is True


def test_subject_abort_ingests_remaining_dropped_frames(
    tmp_path: Path,
) -> None:
    resolved = load_experiment(default_config_path())
    runtime = SessionRuntime(
        resolved,
        "ABORTTIMING",
        output_root=tmp_path,
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
    runtime.acknowledge_frame(
        FrameAcknowledgementPayload(
            previous_presentation_id=first,
            presentation_id=successor.presentation_id,
            subject_monotonic_ns=2_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            frame_index=2,
        ),
        revision=runtime.engine.presentation_revision,
    )
    active = runtime.engine.presentation_id
    assert runtime.engine.diagnostic_state()["trial_id"] is not None

    runtime.confirm_subject_abort(
        FrameAcknowledgementPayload(
            previous_presentation_id=active,
            presentation_id=None,
            neutral=True,
            subject_monotonic_ns=3_000_000_000,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
            frame_index=10,
            dropped_frames=1,
            frame_intervals_seconds=((1 / 60,) * 7 + (0.1172,)),
        ),
        revision=runtime.engine.presentation_revision,
        reason="Escape pressed",
    )

    assert runtime.engine.state == RunState.ABORTED
    quality = json.loads(
        (runtime.session_path / "presentation-metadata.json").read_text(encoding="utf-8")
    )["active_timing_quality"]
    assert quality["dropped_frame_count"] == 1
    assert quality["affected_trial_attempts"][0]["presentation_ids"] == [active]


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
        and engine.current_action.context.stage_type == "practice"
        and engine.current_action.context.phase is not None
        and engine.current_action.context.phase.value == "thinking"
    ):
        acknowledge_boundary()
    pause_and_repeat("repeat_current_trial")

    while not (
        engine.current_action is not None
        and engine.current_action.context.stage_type == "experiment"
        and engine.current_action.context.phase is not None
        and engine.current_action.context.phase.value == "stimulus"
        and engine.current_action.context.attempt == 1
    ):
        if engine.state == RunState.AWAITING_EXPERIMENT:
            engine.start_experiment()
            acknowledge_onset()
        else:
            acknowledge_boundary()
    pause_and_repeat("repeat_current_trial")

    while not (
        engine.current_action is not None
        and engine.current_action.context.stage_type == "experiment"
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
