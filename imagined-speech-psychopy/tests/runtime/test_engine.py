from datetime import UTC, datetime
from pathlib import Path

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.runtime.clock import VirtualClock
from imagined_speech.runtime.protocol import (
    ProtocolEngine,
    RunState,
    _display_instruction,
    _headline,
    build_runtime_actions,
)
from imagined_speech.events import EventType, MemoryEventSink
from imagined_speech.planning import compile_session_plan
from imagined_speech.runtime.simulation import run_virtual


RESOURCE_ROOT = Path(__file__).parents[2] / "src" / "imagined_speech" / "resources"


def make_engine() -> tuple[ProtocolEngine, VirtualClock, MemoryEventSink]:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    sink = MemoryEventSink()
    engine = ProtocolEngine("test-session", plan, resolved.config, clock, sink)
    return engine, clock, sink


def test_fixation_phase_uses_cross_headline() -> None:
    assert _headline("fixation", "/p/") == "+"
    assert _display_instruction("fixation", "Prepare to speak") == ""


def test_cyton_trial_runtime_order_includes_silent_post_trial_gap() -> None:
    resolved = load_experiment(
        RESOURCE_ROOT / "configs" / "cyton-four-phoneme.yaml"
    )
    plan = compile_session_plan(resolved.config)
    screens = [
        action.screen
        for action in build_runtime_actions(plan)
        if hasattr(action, "screen")
    ]
    first_stimulus = screens.index("stimulus")

    first_fixation = first_stimulus - 1
    assert screens[first_fixation : first_fixation + 8] == [
        "fixation",
        "stimulus",
        "fixation",
        "thinking",
        "fixation",
        "speaking",
        "rest",
        "post_trial",
    ]


def test_virtual_run_emits_complete_nested_sequence() -> None:
    engine, clock, sink = make_engine()

    run_virtual(engine)

    assert engine.state == RunState.COMPLETED
    assert clock.monotonic() == pytest.approx(14)
    assert sink.events[0].event_type == EventType.SESSION_STARTED
    assert sink.events[-1].event_type == EventType.SESSION_COMPLETED
    assert [event.sequence_number for event in sink.events] == list(
        range(1, len(sink.events) + 1)
    )
    assert [event.monotonic_seconds for event in sink.events] == sorted(
        event.monotonic_seconds for event in sink.events
    )

    stimulus_indexes = [
        index
        for index, event in enumerate(sink.events)
        if event.event_type == EventType.STIMULUS_PRESENTED
    ]
    assert len(stimulus_indexes) == 3
    for index in stimulus_indexes:
        assert sink.events[index - 1].event_type == EventType.PHASE_STARTED
        assert sink.events[index - 1].monotonic_seconds == sink.events[index].monotonic_seconds


def test_pause_freezes_remaining_time_and_resume_continues() -> None:
    engine, clock, sink = make_engine()
    engine.start()
    clock.advance(0.4)

    engine.pause()
    paused_remaining = engine.remaining_seconds
    clock.advance(100)
    engine.tick()

    assert engine.state == RunState.PAUSED
    assert engine.remaining_seconds == pytest.approx(paused_remaining)

    engine.resume()
    clock.advance(paused_remaining)
    engine.tick()

    assert engine.state == RunState.RUNNING
    assert EventType.SESSION_PAUSED in [event.event_type for event in sink.events]
    assert EventType.SESSION_RESUMED in [event.event_type for event in sink.events]


def test_abort_is_terminal() -> None:
    engine, _, sink = make_engine()
    engine.start()
    engine.abort()

    assert engine.state == RunState.ABORTED
    assert sink.events[-1].event_type == EventType.SESSION_ABORTED
    with pytest.raises(RuntimeError):
        engine.resume()


def test_repeat_trial_preserves_superseded_attempt_and_restarts_paused() -> None:
    engine, clock, sink = make_engine()
    engine.start()
    clock.advance(engine.remaining_seconds)
    engine.tick()
    assert engine.current_action is not None
    assert engine.current_action.context.trial_id is not None

    engine.pause()
    repeated_trial = engine.current_action.context.trial_id
    engine.repeat_current_trial()

    assert engine.state == RunState.PAUSED
    assert engine.current_action is not None
    assert engine.current_action.context.trial_id == repeated_trial
    assert engine.current_action.context.attempt == 2
    engine.resume()
    while engine.state == RunState.RUNNING:
        clock.advance(engine.remaining_seconds)
        engine.tick()

    outcomes = [
        event.payload.get("outcome")
        for event in sink.events
        if event.event_type == EventType.TRIAL_ENDED
        and event.trial_id == repeated_trial
    ]
    assert outcomes == ["superseded", "completed"]
    assert EventType.TRIAL_REPEATED in [event.event_type for event in sink.events]


def test_repeat_block_restarts_trial_order_with_new_attempts() -> None:
    engine, clock, sink = make_engine()
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

    assert engine.state == RunState.COMPLETED
    assert EventType.BLOCK_REPEATED in [event.event_type for event in sink.events]
    experiment_starts = [
        event
        for event in sink.events
        if event.event_type == EventType.TRIAL_STARTED
        and event.block_type == "experiment"
    ]
    assert len(experiment_starts) == 3
    assert experiment_starts[0].trial_id == experiment_starts[1].trial_id
    assert [experiment_starts[0].attempt, experiment_starts[1].attempt] == [1, 2]


def test_abort_closes_active_protocol_scopes() -> None:
    engine, clock, sink = make_engine()
    engine.start()
    clock.advance(engine.remaining_seconds)
    engine.tick()

    engine.abort()

    terminal_types = [event.event_type for event in sink.events[-4:]]
    assert terminal_types == [
        EventType.PHASE_ENDED,
        EventType.TRIAL_ENDED,
        EventType.BLOCK_ENDED,
        EventType.SESSION_ABORTED,
    ]
