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
    block_id: str
    trial_number: int = Field(ge=1)
    block_trial_number: int = Field(ge=1)
    stimulus_id: str
    stimulus_label: str
    phases: tuple[PhaseStep, ...]


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


class BlockPlan(StrictModel):
    kind: Literal["block"] = "block"
    block_id: str
    block_type: Literal["practice", "experiment"]
    block_number: int = Field(ge=0)
    block_count: int = Field(ge=1)
    trials: tuple[TrialPlan, ...] = Field(min_length=1)


PlanItem = Annotated[RestPlan | BreakPlan | BlockPlan, Field(discriminator="kind")]


class SessionPlan(StrictModel):
    schema_version: Literal[1, 2] = 2
    plan_id: str
    config_hash: str
    experiment_id: str
    profile: str
    random_seed: int
    items: tuple[PlanItem, ...]

    @property
    def blocks(self) -> tuple[BlockPlan, ...]:
        return tuple(item for item in self.items if isinstance(item, BlockPlan))

    @property
    def experiment_blocks(self) -> tuple[BlockPlan, ...]:
        return tuple(block for block in self.blocks if block.block_type == "experiment")

    @property
    def practice_blocks(self) -> tuple[BlockPlan, ...]:
        return tuple(block for block in self.blocks if block.block_type == "practice")

    @property
    def experiment_trial_count(self) -> int:
        return sum(len(block.trials) for block in self.experiment_blocks)

    @property
    def practice_trial_count(self) -> int:
        return sum(len(block.trials) for block in self.practice_blocks)

    @property
    def total_trial_count(self) -> int:
        return self.experiment_trial_count + self.practice_trial_count

    @property
    def total_duration_seconds(self) -> float:
        total = 0.0
        for item in self.items:
            if isinstance(item, BlockPlan):
                total += sum(
                    phase.duration_seconds
                    for trial in item.trials
                    for phase in trial.phases
                )
            else:
                total += item.duration_seconds
        return total
