from __future__ import annotations

import pytest

from psychopy_ssvep.config import load_experiment
from psychopy_ssvep.config.models import Waveform
from psychopy_ssvep.events.bus import MemoryEventSink
from psychopy_ssvep.planning import compile_session_plan
from psychopy_ssvep.runtime.clock import VirtualClock
from psychopy_ssvep.runtime.protocol import ProtocolRuntime


def test_frequency_validation_alias_is_two_12_75_hz_trials() -> None:
    resolved = load_experiment("frequency-validation")
    plan = compile_session_plan(resolved.config)

    assert resolved.config_path.name == "12-75.yaml"
    assert [stimulus.frequency_hz for stimulus in resolved.config.stimuli] == [12.75]
    assert resolved.config.stimuli[0].waveform == Waveform.SQUARE
    assert plan.trial_count == 2
    assert plan.duration_seconds == 28.0
    assert resolved.config.stimuli[0].visual.resolve_rect(1920, 1080) == (
        1520,
        680,
        400,
        400,
    )


@pytest.mark.parametrize(
    ("name", "waveform"),
    [
        ("frequency-validation/8-15-square", Waveform.SQUARE),
        ("frequency-validation/8-15-sinusoidal", Waveform.SINUSOIDAL),
    ],
)
def test_frequency_sweeps_check_8_through_15_hz_twice(
    name: str,
    waveform: Waveform,
) -> None:
    resolved = load_experiment(name)
    plan = compile_session_plan(resolved.config)
    expected = [float(value) for value in range(8, 16)]
    stimuli = {stimulus.id: stimulus for stimulus in resolved.config.stimuli}
    targets = [stimuli[f"freq-{value}"] for value in range(8, 16)]

    assert [stimulus.frequency_hz for stimulus in targets] == expected
    assert [stimulus.waveform for stimulus in resolved.config.stimuli] == [waveform] * 11
    assert [
        next(
            stimulus.frequency_hz
            for stimulus in targets
            if stimulus.id == stimulus_id
        )
        for stimulus_id in resolved.config.ordered_stimulus_ids
    ] == expected * 2
    assert plan.trial_count == 16
    assert plan.duration_seconds == 210.0
    assert all(
        stimulus.visual.resolve_rect(1920, 1080) == (1520, 680, 400, 400)
        for stimulus in targets
    )
    assert set(resolved.config.protocol.simultaneous_stimulus_ids or ()) == {
        "distractor-8-25",
        "distractor-11-25",
        "distractor-14-25",
    }


def test_psychopy_sweep_scene_adds_distractors_to_selected_target() -> None:
    resolved = load_experiment("frequency-validation/8-15-square")
    clock = VirtualClock()
    runtime = ProtocolRuntime(
        "session",
        compile_session_plan(resolved.config),
        resolved.config,
        clock,
        MemoryEventSink(),
    )
    runtime.start()
    clock.advance(3.0)
    runtime.tick()

    cue = runtime.view_state()
    assert cue.scene is not None
    assert [node.stimulus.id for node in cue.scene.nodes] == [
        "freq-8",
        "distractor-8-25",
        "distractor-11-25",
        "distractor-14-25",
    ]
    assert [node.highlighted for node in cue.scene.nodes] == [True, False, False, False]
