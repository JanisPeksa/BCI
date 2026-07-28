from __future__ import annotations

from collections import Counter

import numpy as np
import pytest
from pydantic import ValidationError

from ssvep_bci.acquisition.base import MarkerRequest
from ssvep_bci.acquisition.synthetic import SyntheticBackend
from ssvep_bci.config import ExperimentConfig, load_experiment
from ssvep_bci.events.bus import MemoryEventSink
from ssvep_bci.events.models import EventType
from ssvep_bci.planning import StepKind, compile_session_plan
from ssvep_bci.runtime.clock import VirtualClock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.protocol import ProtocolRuntime, RunState


CONFIG_PATH = (
    "src/ssvep_bci/resources/configs/"
    "four-frequency-four-square-4sec-4min.yaml"
)
SIX_SQUARE_CONFIG_PATH = (
    "src/ssvep_bci/resources/configs/"
    "six-frequency-six-square-4sec-4min.yaml"
)
STIMULUS_IDS = {
    "freq-8-75",
    "freq-9-75",
    "freq-12-75",
    "freq-13-75",
}
POSITION_IDS = {
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
}


def test_four_square_profile_compiles_balanced_reproducible_target_positions() -> None:
    resolved = load_experiment(CONFIG_PATH)
    plan = compile_session_plan(resolved.config)
    stimulus_steps = [
        step for step in plan.steps if step.kind == StepKind.STIMULUS
    ]
    target_positions = [step.target_position_id for step in stimulus_steps]

    assert plan == compile_session_plan(resolved.config)
    assert plan.trial_count == 48
    assert Counter(target_positions) == Counter(
        {position_id: 12 for position_id in POSITION_IDS}
    )
    assignments = Counter(
        step.position_stimulus_ids for step in stimulus_steps
    )
    assert len(assignments) == 24
    assert set(assignments.values()) == {2}
    for start in range(0, 48, 4):
        assert set(target_positions[start : start + 4]) == POSITION_IDS
    for step in stimulus_steps:
        assert step.stimulus_id == "freq-12-75"
        assert step.position_stimulus_ids is not None
        assert set(step.position_stimulus_ids) == STIMULUS_IDS


def test_multi_stimulus_validation_rejects_bad_references_and_position_count() -> None:
    resolved = load_experiment(CONFIG_PATH)
    unknown = resolved.config.model_dump(mode="python")
    unknown["multi_stimulus"]["distractor_stimulus_ids"] = (
        "freq-8-75",
        "freq-9-75",
        "missing",
    )
    with pytest.raises(ValidationError, match="unknown stimulus IDs"):
        ExperimentConfig.model_validate(unknown)

    wrong_count = resolved.config.model_dump(mode="python")
    wrong_count["multi_stimulus"]["positions"] = wrong_count[
        "multi_stimulus"
    ]["positions"][:3]
    with pytest.raises(ValidationError, match="total stimulus count"):
        ExperimentConfig.model_validate(wrong_count)


def test_four_square_runtime_cues_one_target_then_flashes_all_four() -> None:
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
    assert len(cue.scene.nodes) == 4
    assert not cue.scene.has_flashing_nodes
    highlighted = [node for node in cue.scene.nodes if node.highlighted]
    assert len(highlighted) == 1
    assert highlighted[0].stimulus.id == "freq-12-75"

    trial_event = next(
        event for event in sink.events if event.event_type == EventType.TRIAL_STARTED
    )
    assert trial_event.stimulus_id == "freq-12-75"
    assert trial_event.payload["target_frequency_hz"] == 12.75
    assert trial_event.payload["target_position_id"] in POSITION_IDS
    assert set(trial_event.payload["distractor_frequencies_hz"]) == {
        8.75,
        9.75,
        13.75,
    }
    assert set(trial_event.payload["stimulus_positions"]) == POSITION_IDS

    clock.advance(1.0)
    runtime.tick()
    stimulus = runtime.view_state()
    assert runtime.state == RunState.AWAITING_ONSET
    assert stimulus.scene is not None
    assert len(stimulus.scene.nodes) == 4
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
    assert onset.stimulus_id == "freq-12-75"
    assert set(onset.payload["stimulus_frequencies_hz"]) == {
        8.75,
        9.75,
        12.75,
        13.75,
    }


def test_synthetic_backend_contains_all_four_simultaneous_frequencies() -> None:
    synthetic_device = load_experiment().device
    clock = VirtualClock()
    backend = SyntheticBackend(synthetic_device, clock)
    backend.prepare()
    backend.start()
    expected = (8.75, 9.75, 12.75, 13.75)
    backend.insert_marker(
        MarkerRequest(
            event_id="multi-onset",
            event_sequence=1,
            event_type="stimulus_onset",
            marker_code=1002,
            event_monotonic_timestamp=0.0,
            event_wall_clock_timestamp_utc=clock.wall_time_utc(),
            stimulus_frequency_hz=12.75,
            stimulus_frequencies_hz=expected,
        )
    )
    clock.advance(4.0)
    batch = backend.read_available(2000)
    assert batch is not None

    frequencies = np.fft.rfftfreq(
        batch.sample_count, d=1.0 / synthetic_device.sampling_rate_hz
    )
    amplitudes = 2.0 * np.abs(np.fft.rfft(batch.eeg[:, 0])) / batch.sample_count
    for expected_frequency in expected:
        index = int(np.argmin(np.abs(frequencies - expected_frequency)))
        assert frequencies[index] == pytest.approx(expected_frequency)
        assert amplitudes[index] > 10.0


def test_six_square_profile_balances_target_and_renders_all_frequencies() -> None:
    resolved = load_experiment(SIX_SQUARE_CONFIG_PATH)
    plan = compile_session_plan(resolved.config)
    stimulus_steps = [
        step for step in plan.steps if step.kind == StepKind.STIMULUS
    ]
    position_ids = {
        "top-left",
        "top-center",
        "top-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    }
    expected_stimuli = {
        "freq-8-75",
        "freq-9-75",
        "freq-10-75",
        "freq-11-75",
        "freq-12-75",
        "freq-13-75",
    }

    assert plan == compile_session_plan(resolved.config)
    assert plan.trial_count == 72
    assert Counter(
        step.target_position_id for step in stimulus_steps
    ) == Counter({position_id: 12 for position_id in position_ids})
    for start in range(0, 72, 6):
        assert {
            step.target_position_id
            for step in stimulus_steps[start : start + 6]
        } == position_ids
    assert all(
        step.stimulus_id == "freq-13-75"
        and step.position_stimulus_ids is not None
        and set(step.position_stimulus_ids) == expected_stimuli
        for step in stimulus_steps
    )

    clock = VirtualClock()
    sink = MemoryEventSink()
    runtime = ProtocolRuntime("session", plan, resolved.config, clock, sink)
    runtime.start()
    clock.advance(5.0)
    runtime.tick()
    cue = runtime.view_state()
    assert cue.scene is not None
    assert len(cue.scene.nodes) == 6
    assert sum(node.highlighted for node in cue.scene.nodes) == 1
    assert not cue.scene.has_flashing_nodes

    clock.advance(1.0)
    runtime.tick()
    stimulation = runtime.view_state()
    assert stimulation.scene is not None
    assert len(stimulation.scene.nodes) == 6
    assert all(node.flashing_requested for node in stimulation.scene.nodes)
    event = next(
        item for item in sink.events if item.event_type == EventType.TRIAL_STARTED
    )
    assert event.payload["target_frequency_hz"] == 13.75
    assert set(event.payload["distractor_frequencies_hz"]) == {
        8.75,
        9.75,
        10.75,
        11.75,
        12.75,
    }
