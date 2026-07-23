from __future__ import annotations

import pytest
from pydantic import ValidationError

from ssvep_bci.config import load_experiment
from ssvep_bci.planning import StepKind, compile_session_plan


def test_default_configuration_and_plan_are_deterministic() -> None:
    resolved = load_experiment()
    first = compile_session_plan(resolved.config)
    second = compile_session_plan(resolved.config)
    assert first == second
    assert first.trial_count == 2
    assert first.duration_seconds == 15
    assert [step.kind for step in first.steps].count(StepKind.STIMULUS) == 2


def test_duplicate_stimulus_frequency_is_rejected() -> None:
    resolved = load_experiment()
    duplicate = resolved.config.active_stimulus.model_copy(update={"id": "target-2"})
    with pytest.raises(ValidationError, match="stimulus frequencies must be unique"):
        resolved.config.model_copy(
            update={"stimuli": (*resolved.config.stimuli, duplicate)}
        ).model_validate(
            {
                **resolved.config.model_dump(),
                "stimuli": [
                    *[item.model_dump() for item in resolved.config.stimuli],
                    duplicate.model_dump(),
                ],
            }
        )


def test_four_frequency_collection_matches_legacy_timing() -> None:
    resolved = load_experiment(
        "src/ssvep_bci/resources/configs/four-frequency-collection.yaml"
    )
    plan = compile_session_plan(resolved.config)
    assert plan == compile_session_plan(resolved.config)
    stimulus_steps = [step for step in plan.steps if step.kind == StepKind.STIMULUS]
    assert plan.trial_count == 20
    assert plan.duration_seconds == 285.0
    targets = [step.stimulus_id for step in stimulus_steps]
    expected = {"freq-8-25", "freq-9-75", "freq-12-75", "freq-14-25"}
    for start in range(0, 20, 4):
        assert set(targets[start:start + 4]) == expected
    assert [step.duration_seconds for step in stimulus_steps] == [5.0] * 20


def test_single_circle_collection_is_balanced_and_exactly_four_minutes() -> None:
    resolved = load_experiment(
        "src/ssvep_bci/resources/configs/four-frequency-single-circle-4min.yaml"
    )
    plan = compile_session_plan(resolved.config)
    assert plan == compile_session_plan(resolved.config)
    stimulus_steps = [step for step in plan.steps if step.kind == StepKind.STIMULUS]
    initial_steps = [step for step in plan.steps if step.kind == StepKind.INITIAL_REST]
    interval_steps = [step for step in plan.steps if step.kind == StepKind.INTER_TRIAL]
    targets = [step.stimulus_id for step in stimulus_steps]
    expected = {"freq-8-25", "freq-9-75", "freq-12-75", "freq-14-25"}
    assert plan.trial_count == 48
    assert plan.duration_seconds == 386.0
    assert len(stimulus_steps) == 48
    assert [step.duration_seconds for step in initial_steps] == [5.0]
    assert len(interval_steps) == 47
    assert all(step.duration_seconds == 3.0 for step in interval_steps)
    assert all(targets.count(target) == 12 for target in expected)
    for start in range(0, 48, 4):
        assert set(targets[start:start + 4]) == expected
    assert [step.duration_seconds for step in stimulus_steps] == [5.0] * 48
