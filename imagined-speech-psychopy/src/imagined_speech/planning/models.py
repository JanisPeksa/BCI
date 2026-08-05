"""Versioned deterministic session-plan contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from imagined_speech.config import Phase, StrictModel


class PhaseStep(StrictModel):
    step_id: str
    phase: Phase
    duration_seconds: float = Field(gt=0)
    instruction: str


class TrialPlan(StrictModel):
    trial_id: str
    trial_number: int = Field(ge=1)
    group_trial_number: int = Field(ge=1)
    stimulus_id: str
    stimulus_label: str
    phases: tuple[PhaseStep, ...]
    post_trial_seconds: float = Field(default=0, ge=0)


class RestPlan(StrictModel):
    kind: Literal["rest"] = "rest"
    rest_id: str
    rest_type: Literal["initial", "final"]
    duration_seconds: float = Field(gt=0)
    instruction: str = "REST"


class BreakPlan(StrictModel):
    kind: Literal["break"] = "break"
    break_id: str
    after_block: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    instruction: str = "BREAK"


class PracticePlan(StrictModel):
    kind: Literal["practice"] = "practice"
    practice_id: str = "practice"
    trials: tuple[TrialPlan, ...] = Field(min_length=1)


class ExperimentBlockPlan(StrictModel):
    kind: Literal["experiment_block"] = "experiment_block"
    block_id: str
    block_number: int = Field(ge=1)
    block_count: int = Field(ge=1)
    trials: tuple[TrialPlan, ...] = Field(min_length=1)


TrialGroupPlan = PracticePlan | ExperimentBlockPlan
PlanItem = Annotated[
    RestPlan | BreakPlan | PracticePlan | ExperimentBlockPlan,
    Field(discriminator="kind"),
]


class SessionPlan(StrictModel):
    schema_version: Literal[3] = 3
    plan_id: str
    config_hash: str
    experiment_id: str
    profile: str
    random_seed: int
    items: tuple[PlanItem, ...]

    @property
    def practice(self) -> PracticePlan | None:
        return next(
            (item for item in self.items if isinstance(item, PracticePlan)),
            None,
        )

    @property
    def trial_groups(self) -> tuple[TrialGroupPlan, ...]:
        return tuple(
            item
            for item in self.items
            if isinstance(item, (PracticePlan, ExperimentBlockPlan))
        )

    @property
    def experiment_blocks(self) -> tuple[ExperimentBlockPlan, ...]:
        return tuple(
            item for item in self.items if isinstance(item, ExperimentBlockPlan)
        )

    @property
    def experiment_trial_count(self) -> int:
        return sum(len(block.trials) for block in self.experiment_blocks)

    @property
    def practice_trial_count(self) -> int:
        return len(self.practice.trials) if self.practice is not None else 0

    @property
    def total_trial_count(self) -> int:
        return self.experiment_trial_count + self.practice_trial_count

    @property
    def total_duration_seconds(self) -> float:
        total = 0.0
        for item in self.items:
            if isinstance(item, (PracticePlan, ExperimentBlockPlan)):
                total += sum(
                    sum(phase.duration_seconds for phase in trial.phases)
                    + trial.post_trial_seconds
                    for trial in item.trials
                )
            else:
                total += item.duration_seconds
        return total
