from __future__ import annotations

from collections import Counter

import numpy as np
import pytest
from pydantic import ValidationError

from ssvep_bci.acquisition.base import MarkerRequest
from ssvep_bci.acquisition.synthetic import SyntheticBackend
from ssvep_bci.config import (
    ExperimentConfig,
    HorizontalLayout,
    TargetSide,
    load_experiment,
)
from ssvep_bci.events.bus import MemoryEventSink
from ssvep_bci.events.models import EventType
from ssvep_bci.planning import StepKind, compile_session_plan
from ssvep_bci.runtime.clock import VirtualClock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.protocol import ProtocolRuntime, RunState
from ssvep_bci.stimuli import StimulusPlacementOverride


CONFIG_PATH = (
    "src/ssvep_bci/resources/configs/"
    "three-frequency-dual-square-4sec-4min.yaml"
)


def test_dual_profile_compiles_deterministic_balanced_blocks() -> None:
    resolved = load_experiment(CONFIG_PATH)
    first = compile_session_plan(resolved.config)
    second = compile_session_plan(resolved.config)
    stimulus_steps = [
        step for step in first.steps if step.kind == StepKind.STIMULUS
    ]
    conditions = [
        (step.target_side, step.distractor_stimulus_id)
        for step in stimulus_steps
    ]
    expected = {
        (TargetSide.LEFT, "freq-8-75"),
        (TargetSide.RIGHT, "freq-8-75"),
        (TargetSide.LEFT, "freq-13-75"),
        (TargetSide.RIGHT, "freq-13-75"),
    }

    assert first == second
    assert first.trial_count == 48
    assert Counter(conditions) == Counter({condition: 12 for condition in expected})
    for start in range(0, len(conditions), 4):
        assert set(conditions[start : start + 4]) == expected


def test_dual_profile_rejects_unknown_references_and_duplicate_conditions() -> None:
    resolved = load_experiment(CONFIG_PATH)
    unknown = resolved.config.model_dump(mode="python")
    unknown["dual_stimulus"]["distractor_stimulus_ids"] = (
        "freq-8-75",
        "missing",
    )
    with pytest.raises(ValidationError, match="unknown stimulus IDs"):
        ExperimentConfig.model_validate(unknown)

    duplicate = resolved.config.model_dump(mode="python")
    duplicate["dual_stimulus"]["conditions"] = [
        {
            "target_side": "left",
            "distractor_stimulus_id": "freq-8-75",
        },
        {
            "target_side": "left",
            "distractor_stimulus_id": "freq-8-75",
        },
    ]
    with pytest.raises(ValidationError, match="conditions must be unique"):
        ExperimentConfig.model_validate(duplicate)


def test_dual_runtime_cues_target_then_flashes_both_and_emits_condition() -> None:
    resolved = load_experiment(CONFIG_PATH)
    clock = VirtualClock()
    sink = MemoryEventSink()
    runtime = ProtocolRuntime(
        "session",
        compile_session_plan(resolved.config),
        resolved.config,
        clock,
        sink,
    )
    runtime.start()

    clock.advance(5.0)
    runtime.tick()
    cue = runtime.view_state()
    assert cue.scene is not None
    assert len(cue.scene.nodes) == 2
    assert not cue.scene.has_flashing_nodes
    highlighted = [node for node in cue.scene.nodes if node.highlighted]
    assert len(highlighted) == 1
    assert highlighted[0].stimulus.id == "freq-11-75"

    trial_event = next(
        event for event in sink.events if event.event_type == EventType.TRIAL_STARTED
    )
    assert trial_event.stimulus_id == "freq-11-75"
    assert trial_event.payload["target_frequency_hz"] == 11.75
    assert trial_event.payload["target_side"] in {"left", "right"}
    assert trial_event.payload["distractor_frequency_hz"] in {8.75, 13.75}
    assert trial_event.payload["distractor_stimulus_id"] in {
        "freq-8-75",
        "freq-13-75",
    }

    clock.advance(3.0)
    runtime.tick()
    stimulus = runtime.view_state()
    assert runtime.state == RunState.AWAITING_ONSET
    assert stimulus.scene is not None
    assert len(stimulus.scene.nodes) == 2
    assert stimulus.scene.has_flashing_nodes
    assert all(node.flashing_requested for node in stimulus.scene.nodes)
    assert not any(node.highlighted for node in stimulus.scene.nodes)

    presentation_id = runtime.current_step.presentation_id
    assert presentation_id is not None
    runtime.acknowledge_frame(
        PresentationFrameAcknowledged(
            presentation_id,
            FrameKind.ONSET,
            clock.monotonic(),
            clock.wall_time_utc(),
        )
    )
    onset = next(
        event for event in sink.events if event.event_type == EventType.STIMULUS_ONSET
    )
    assert onset.stimulus_id == "freq-11-75"
    assert onset.payload["target_frequency_hz"] == 11.75
    assert onset.payload["target_side"] == trial_event.payload["target_side"]
    assert (
        onset.payload["distractor_frequency_hz"]
        == trial_event.payload["distractor_frequency_hz"]
    )


def test_every_dual_trial_runtime_scene_contains_two_nodes() -> None:
    resolved = load_experiment(CONFIG_PATH)
    clock = VirtualClock()
    runtime = ProtocolRuntime(
        "session",
        compile_session_plan(resolved.config),
        resolved.config,
        clock,
        MemoryEventSink(),
    )
    runtime.start()
    clock.advance(5.0)
    runtime.tick()

    for trial_index in range(48):
        cue = runtime.view_state()
        assert cue.phase == StepKind.PRE_STIMULUS.value
        assert cue.scene is not None
        assert len(cue.scene.nodes) == 2

        clock.advance(3.0)
        runtime.tick()
        stimulus = runtime.view_state()
        assert stimulus.phase == StepKind.STIMULUS.value
        assert stimulus.scene is not None
        assert len(stimulus.scene.nodes) == 2

        presentation_id = runtime.current_step.presentation_id
        assert presentation_id is not None
        runtime.acknowledge_frame(
            PresentationFrameAcknowledged(
                presentation_id,
                FrameKind.ONSET,
                clock.monotonic(),
                clock.wall_time_utc(),
            )
        )
        clock.advance(4.0)
        runtime.tick()
        runtime.acknowledge_frame(
            PresentationFrameAcknowledged(
                presentation_id,
                FrameKind.OFFSET,
                clock.monotonic(),
                clock.wall_time_utc(),
            )
        )
        if trial_index < 47:
            clock.advance(3.0)
            runtime.tick()

    assert runtime.state == RunState.COMPLETED


def test_equal_gap_and_manual_placement_resolve_from_viewport() -> None:
    left = StimulusPlacementOverride(
        side=TargetSide.LEFT,
        width_px=200,
        height_px=200,
        center_y=0.5,
        horizontal_layout=HorizontalLayout.EQUAL_GAPS,
    ).resolve_rect(1920, 1080)
    right = StimulusPlacementOverride(
        side=TargetSide.RIGHT,
        width_px=200,
        height_px=200,
        center_y=0.5,
        horizontal_layout=HorizontalLayout.EQUAL_GAPS,
    ).resolve_rect(1920, 1080)
    edge_left = left[0]
    between = right[0] - (left[0] + left[2])
    edge_right = 1920 - (right[0] + right[2])
    assert max(edge_left, between, edge_right) - min(
        edge_left, between, edge_right
    ) <= 1
    assert left[1:] == (440, 200, 200)
    assert right[1:] == (440, 200, 200)

    manual = StimulusPlacementOverride(
        side=TargetSide.LEFT,
        width_px=100,
        height_px=80,
        center_y=0.25,
        horizontal_layout=HorizontalLayout.MANUAL,
        center_x=0.2,
    )
    assert manual.resolve_rect(1000, 800) == (150, 160, 100, 80)


def test_synthetic_backend_contains_target_and_distractor_frequencies() -> None:
    resolved = load_experiment(CONFIG_PATH)
    clock = VirtualClock()
    backend = SyntheticBackend(resolved.device, clock)
    backend.prepare()
    backend.start()
    backend.insert_marker(
        MarkerRequest(
            event_id="dual-onset",
            event_sequence=1,
            event_type="stimulus_onset",
            marker_code=1001,
            event_monotonic_timestamp=0.0,
            event_wall_clock_timestamp_utc=clock.wall_time_utc(),
            stimulus_frequency_hz=11.75,
            distractor_frequency_hz=8.75,
        )
    )
    clock.advance(4.0)
    batch = backend.read_available(2000)
    assert batch is not None

    frequencies = np.fft.rfftfreq(
        batch.sample_count, d=1.0 / resolved.device.sampling_rate_hz
    )
    amplitudes = 2.0 * np.abs(np.fft.rfft(batch.eeg[:, 0])) / batch.sample_count
    for expected in (8.75, 11.75):
        index = int(np.argmin(np.abs(frequencies - expected)))
        assert frequencies[index] == pytest.approx(expected)
        assert amplitudes[index] > 10.0
