from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from psychopy_ssvep.config.models import StrictModel, TargetSide


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
    target_side: TargetSide | None = None
    distractor_stimulus_id: str | None = None
    target_position_id: str | None = None
    position_stimulus_ids: tuple[str, ...] | None = Field(
        default=None, min_length=2
    )

    @model_validator(mode="after")
    def validate_condition(self) -> "PlanStep":
        has_side = self.target_side is not None
        has_distractor = self.distractor_stimulus_id is not None
        if has_side != has_distractor:
            raise ValueError(
                "target_side and distractor_stimulus_id must be set together"
            )
        if has_side and self.stimulus_id is None:
            raise ValueError("a dual-stimulus condition requires stimulus_id")
        has_position = self.target_position_id is not None
        has_assignments = self.position_stimulus_ids is not None
        if has_position != has_assignments:
            raise ValueError(
                "target_position_id and position_stimulus_ids must be set together"
            )
        if has_position and self.stimulus_id is None:
            raise ValueError("a multi-stimulus condition requires stimulus_id")
        if has_assignments:
            assert self.position_stimulus_ids is not None
            if len(set(self.position_stimulus_ids)) != len(
                self.position_stimulus_ids
            ):
                raise ValueError(
                    "multi-stimulus position assignments must be unique"
                )
            if self.position_stimulus_ids.count(self.stimulus_id) != 1:
                raise ValueError(
                    "multi-stimulus assignments must contain the target exactly once"
                )
        if has_side and has_position:
            raise ValueError("dual and multi-stimulus conditions are mutually exclusive")
        return self


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
