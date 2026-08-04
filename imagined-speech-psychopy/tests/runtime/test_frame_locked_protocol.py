from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.events import EventType, MemoryEventSink
from imagined_speech.planning import compile_session_plan
from imagined_speech.runtime.clock import VirtualClock
from imagined_speech.runtime.protocol import FrameLockedProtocolEngine, RunState


def make_engine() -> tuple[FrameLockedProtocolEngine, MemoryEventSink, VirtualClock]:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    sink = MemoryEventSink()
    engine = FrameLockedProtocolEngine(
        "session-test", plan, resolved.config, clock, sink
    )
    return engine, sink, clock


def acknowledge_onset(engine: FrameLockedProtocolEngine, at: float = 1.0) -> None:
    engine.acknowledge_frame(
        previous_presentation_id=None,
        presentation_id=engine.presentation_id,
        revision=engine.presentation_revision,
        monotonic_seconds=at,
        wall_time_utc=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=at),
        neutral=False,
    )


def test_boundary_commits_old_end_before_new_start_at_one_flip() -> None:
    engine, sink, _ = make_engine()
    engine.start()
    assert engine.state == RunState.AWAITING_PRESENTATION
    acknowledge_onset(engine)
    previous = engine.presentation_id
    successor = engine.successor_view_state()
    assert successor is not None

    engine.acknowledge_frame(
        previous_presentation_id=previous,
        presentation_id=successor.presentation_id,
        revision=engine.presentation_revision,
        monotonic_seconds=3.0,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        neutral=False,
    )

    boundary = [event for event in sink.events if event.monotonic_seconds == 3.0]
    assert len(boundary) >= 2
    end_index = next(
        index for index, event in enumerate(boundary)
        if event.event_type in {EventType.REST_ENDED, EventType.PHASE_ENDED}
    )
    start_index = next(
        index for index, event in enumerate(boundary)
        if event.event_type in {EventType.REST_STARTED, EventType.PHASE_STARTED}
    )
    assert end_index < start_index
    assert engine.presentation_id == successor.presentation_id


def test_stale_ack_is_rejected_without_advancing() -> None:
    engine, _, _ = make_engine()
    engine.start()
    presentation_id = engine.presentation_id
    with pytest.raises(ValueError, match="stale"):
        engine.acknowledge_frame(
            previous_presentation_id=None,
            presentation_id=presentation_id,
            revision=engine.presentation_revision - 1,
            monotonic_seconds=1,
            wall_time_utc=datetime(2026, 1, 1, tzinfo=UTC),
            neutral=False,
        )
    assert engine.state == RunState.AWAITING_PRESENTATION
    assert engine.presentation_id == presentation_id


def test_pause_requires_neutral_ack_and_resume_requires_new_onset() -> None:
    engine, _, _ = make_engine()
    engine.start()
    acknowledge_onset(engine)
    active = engine.presentation_id
    engine.pause()
    assert engine.state == RunState.AWAITING_NEUTRAL
    engine.acknowledge_frame(
        previous_presentation_id=active,
        presentation_id=None,
        revision=engine.presentation_revision,
        monotonic_seconds=1.5,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=UTC),
        neutral=True,
    )
    assert engine.state == RunState.PAUSED
    engine.resume()
    resumed = engine.presentation_id
    assert resumed != active
    acknowledge_onset(engine, 2.0)
    assert engine.state == RunState.RUNNING


@pytest.mark.parametrize(
    "repeat_method",
    ("repeat_current_trial", "repeat_current_block"),
)
def test_repeat_from_pause_reuses_neutral_frame_and_waits_for_resume(
    repeat_method: str,
) -> None:
    engine, sink, _ = make_engine()
    engine.start()
    acknowledge_onset(engine)
    if engine.current_action is not None and engine.current_action.context.trial_id is None:
        successor = engine.successor_view_state()
        assert successor is not None
        engine.acknowledge_frame(
            previous_presentation_id=engine.presentation_id,
            presentation_id=successor.presentation_id,
            revision=engine.presentation_revision,
            monotonic_seconds=2.0,
            wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
            neutral=False,
        )
    assert engine.current_action is not None
    assert engine.current_action.context.trial_id is not None
    active = engine.presentation_id
    engine.pause()
    engine.acknowledge_frame(
        previous_presentation_id=active,
        presentation_id=None,
        revision=engine.presentation_revision,
        monotonic_seconds=2.5,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, 500000, tzinfo=UTC),
        neutral=True,
    )
    assert engine.state == RunState.PAUSED
    assert engine.presentation_id is None

    getattr(engine, repeat_method)()
    diagnostic = engine.diagnostic_state()

    assert diagnostic["engine_state"] == "paused"
    assert diagnostic["pending_control"] is None
    assert diagnostic["presentation_id"] is None
    assert diagnostic["attempt"] == 2

    engine.resume()
    assert engine.state == RunState.AWAITING_PRESENTATION
    acknowledge_onset(engine, 3.0)
    assert engine.state == RunState.RUNNING
    resumed_boundary = [
        event.event_type
        for event in sink.events
        if event.monotonic_seconds == 3.0
    ]
    assert resumed_boundary == [
        EventType.SESSION_RESUMED,
        EventType.TRIAL_STARTED,
        EventType.PHASE_STARTED,
    ]


def test_abort_from_pause_reuses_acknowledged_neutral_frame() -> None:
    engine, _, _ = make_engine()
    engine.start()
    acknowledge_onset(engine)
    active = engine.presentation_id
    engine.pause()
    engine.acknowledge_frame(
        previous_presentation_id=active,
        presentation_id=None,
        revision=engine.presentation_revision,
        monotonic_seconds=1.5,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=UTC),
        neutral=True,
    )

    engine.abort()

    assert engine.state == RunState.ABORTED
    assert engine.presentation_id is None


def test_chained_repeats_before_resume_reuse_unpresented_attempt_number() -> None:
    engine, sink, _ = make_engine()
    engine.start()
    acknowledge_onset(engine)
    successor = engine.successor_view_state()
    assert successor is not None
    engine.acknowledge_frame(
        previous_presentation_id=engine.presentation_id,
        presentation_id=successor.presentation_id,
        revision=engine.presentation_revision,
        monotonic_seconds=2.0,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        neutral=False,
    )
    active = engine.presentation_id
    engine.pause()
    engine.acknowledge_frame(
        previous_presentation_id=active,
        presentation_id=None,
        revision=engine.presentation_revision,
        monotonic_seconds=2.5,
        wall_time_utc=datetime(2026, 1, 1, 0, 0, 2, 500000, tzinfo=UTC),
        neutral=True,
    )

    engine.repeat_current_block()
    assert engine.current_action is not None
    assert engine.current_action.context.attempt == 2
    engine.repeat_current_trial()
    assert engine.current_action is not None
    assert engine.current_action.context.attempt == 2
    engine.resume()
    acknowledge_onset(engine, 3.0)

    started_attempts = [
        event.attempt
        for event in sink.events
        if event.event_type == EventType.TRIAL_STARTED
    ]
    assert started_attempts == [1, 2]
