from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from ssvep_bci.config.models import StrictModel


class StepKind(StrEnum):
    INITIAL_REST = "initial_rest"
    PRE_STIMULUS = "pre_stimulus"
    STIMULUS = "stimulus"
    INTER_TRIAL = "inter_trial"
    FINAL_REST = "final_rest"


class PlanStep(StrictModel):
    step_id: str
    kind: StepKind
    duration_seconds: float = Field(ge=0)
    trial_id: str | None = None
    trial_number: int | None = Field(default=None, ge=1)
    stimulus_id: str | None = None
    presentation_id: str | None = None


class SessionPlan(StrictModel):
    schema_version: Literal[1] = 1
    plan_id: str
    config_hash: str
    experiment_id: str
    steps: tuple[PlanStep, ...]
    trial_count: int = Field(ge=1)

    @property
    def duration_seconds(self) -> float:
        return sum(step.duration_seconds for step in self.steps)

