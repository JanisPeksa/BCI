from __future__ import annotations

from ssvep_bci.config import load_experiment
from ssvep_bci.events.bus import MemoryEventSink
from ssvep_bci.events.models import EventType
from ssvep_bci.planning import compile_session_plan
from ssvep_bci.runtime.clock import VirtualClock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.protocol import ProtocolRuntime, RunState
from ssvep_bci.ui.subject_window import format_trial_progress


def test_stimulus_interval_starts_and_ends_on_frame_acknowledgements() -> None:
    resolved = load_experiment()
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock()
    sink = MemoryEventSink()
    runtime = ProtocolRuntime("session", plan, resolved.config, clock, sink)
    runtime.start()
    clock.advance(2.0)
    runtime.tick()
    assert runtime.state == RunState.AWAITING_ONSET
    view = runtime.view_state()
    assert view.scene is not None
    assert view.scene.has_visible_nodes
    assert len(view.scene.nodes) == 1
    assert view.scene.nodes[0].stimulus.id == resolved.config.protocol.active_stimulus_id
    presentation_id = runtime.current_step.presentation_id
    assert presentation_id
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        presentation_id,
        FrameKind.ONSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))
    onset = next(event for event in sink.events if event.event_type == EventType.STIMULUS_ONSET)
    assert onset.monotonic_timestamp == 2.0
    clock.advance(5.0)
    runtime.tick()
    assert runtime.state == RunState.AWAITING_OFFSET
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        presentation_id,
        FrameKind.OFFSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))
    offset = next(event for event in sink.events if event.event_type == EventType.STIMULUS_OFFSET)
    assert offset.monotonic_timestamp == 7.0
    assert offset.payload["confirmed_by_frame_swap"] is True


def test_collection_cues_one_target_then_flashes_four_target_scene() -> None:
    resolved = load_experiment(
        "src/ssvep_bci/resources/configs/four-frequency-collection.yaml"
    )
    clock = VirtualClock()
    runtime = ProtocolRuntime(
        "session",
        compile_session_plan(resolved.config),
        resolved.config,
        clock,
        MemoryEventSink(),
    )
    runtime.start()
    clock.advance(10.0)
    runtime.tick()
    cue = runtime.view_state()
    assert cue.scene is not None
    assert len(cue.scene.nodes) == 4
    assert not cue.scene.has_flashing_nodes
    assert sum(node.highlighted for node in cue.scene.nodes) == 1

    clock.advance(5.0)
    runtime.tick()
    stimulus = runtime.view_state()
    assert runtime.state == RunState.AWAITING_ONSET
    assert stimulus.scene is not None
    assert len(stimulus.scene.nodes) == 4
    assert stimulus.scene.has_flashing_nodes
    assert not any(node.highlighted for node in stimulus.scene.nodes)


def test_progress_is_shown_only_during_post_trial_rest() -> None:
    resolved = load_experiment()
    clock = VirtualClock()
    runtime = ProtocolRuntime(
        "session",
        compile_session_plan(resolved.config),
        resolved.config,
        clock,
        MemoryEventSink(),
    )
    runtime.start()

    initial_rest = runtime.view_state()
    assert initial_rest.message == "Prepare"
    assert initial_rest.completed_trial_count == 0
    assert format_trial_progress(initial_rest) == ""

    clock.advance(1.0)
    runtime.tick()
    pre_stimulus = runtime.view_state()
    assert pre_stimulus.message == "Focus on the stimulus location"
    assert format_trial_progress(pre_stimulus) == ""

    clock.advance(1.0)
    runtime.tick()
    first_stimulus = runtime.view_state()
    assert first_stimulus.message == ""
    assert format_trial_progress(first_stimulus) == ""

    presentation_id = runtime.current_step.presentation_id
    assert presentation_id
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        presentation_id,
        FrameKind.ONSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))
    clock.advance(5.0)
    runtime.tick()
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        presentation_id,
        FrameKind.OFFSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))

    inter_trial_rest = runtime.view_state()
    assert inter_trial_rest.message == "Rest"
    assert inter_trial_rest.completed_trial_count == 1
    assert format_trial_progress(inter_trial_rest) == "Trial 1/2 completed"

    clock.advance(1.0)
    runtime.tick()
    second_pre_stimulus = runtime.view_state()
    assert second_pre_stimulus.completed_trial_count == 1
    assert format_trial_progress(second_pre_stimulus) == ""

    clock.advance(1.0)
    runtime.tick()
    second_presentation_id = runtime.current_step.presentation_id
    assert second_presentation_id
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        second_presentation_id,
        FrameKind.ONSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))
    clock.advance(5.0)
    runtime.tick()
    runtime.acknowledge_frame(PresentationFrameAcknowledged(
        second_presentation_id,
        FrameKind.OFFSET,
        clock.monotonic(),
        clock.wall_time_utc(),
    ))

    final_rest = runtime.view_state()
    assert final_rest.message == "Complete"
    assert final_rest.completed_trial_count == 2
    assert format_trial_progress(final_rest) == "Trial 2/2 completed"
