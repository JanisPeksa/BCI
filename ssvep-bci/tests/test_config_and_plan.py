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

