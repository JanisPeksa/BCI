from datetime import UTC, datetime

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.engine import ProtocolEngine, RunState, VirtualClock
from imagined_speech.events import EventType, MemoryEventSink
from imagined_speech.plan import compile_session_plan
from imagined_speech.simulation import run_virtual


def make_engine() -> tuple[ProtocolEngine, VirtualClock, MemoryEventSink]:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    sink = MemoryEventSink()
    engine = ProtocolEngine("test-session", plan, resolved.config, clock, sink)
    return engine, clock, sink


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
