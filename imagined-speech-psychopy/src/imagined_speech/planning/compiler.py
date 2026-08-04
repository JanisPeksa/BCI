"""Pure deterministic compiler from experiment configuration to session plan."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Literal

from imagined_speech.config import ExperimentConfig, StimulusConfig
from imagined_speech.planning.models import (
    BlockPlan,
    BreakPlan,
    PhaseStep,
    PlanItem,
    RestPlan,
    SessionPlan,
    TrialPlan,
)


def config_fingerprint(config: ExperimentConfig) -> str:
    canonical = json.dumps(
        config.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _phase_steps(config: ExperimentConfig, trial_id: str) -> tuple[PhaseStep, ...]:
    return tuple(
        PhaseStep(
            step_id=f"{trial_id}-phase-{phase.value}",
            phase=phase,
            duration_seconds=config.phases[phase].duration_seconds,
            instruction=config.phases[phase].instruction,
        )
        for phase in config.phase_sequence
    )


def _practice_stimuli(config: ExperimentConfig) -> list[StimulusConfig]:
    by_id = {stimulus.id: stimulus for stimulus in config.stimuli}
    stimuli = [
        by_id[stimulus_id]
        for stimulus_id in config.protocol.practice_stimulus_ids
        for _ in range(config.protocol.practice_repetitions_per_stimulus)
    ]
    random.Random(f"{config.random_seed}:practice").shuffle(stimuli)
    return stimuli


def _experiment_stimuli_by_block(config: ExperimentConfig) -> list[list[StimulusConfig]]:
    block_count = config.protocol.blocks
    base, remainder = divmod(config.protocol.repetitions_per_stimulus, block_count)
    blocks: list[list[StimulusConfig]] = [[] for _ in range(block_count)]
    for stimulus in config.stimuli:
        for block in blocks:
            block.extend([stimulus] * base)
    cursor = config.random_seed % block_count
    for stimulus in config.stimuli:
        for offset in range(remainder):
            blocks[(cursor + offset) % block_count].append(stimulus)
        cursor = (cursor + remainder) % block_count
    for index, block in enumerate(blocks, start=1):
        random.Random(f"{config.random_seed}:block:{index}").shuffle(block)
    return blocks


def _make_block(
    config: ExperimentConfig,
    block_id: str,
    block_type: Literal["practice", "experiment"],
    block_number: int,
    stimuli: list[StimulusConfig],
    first_trial_number: int,
) -> BlockPlan:
    trials: list[TrialPlan] = []
    for block_trial_number, stimulus in enumerate(stimuli, start=1):
        trial_number = first_trial_number + block_trial_number - 1
        trial_id = f"{block_id}-trial-{block_trial_number:03d}"
        trials.append(TrialPlan(
            trial_id=trial_id,
            block_id=block_id,
            trial_number=trial_number,
            block_trial_number=block_trial_number,
            stimulus_id=stimulus.id,
            stimulus_label=stimulus.label,
            phases=_phase_steps(config, trial_id),
        ))
    return BlockPlan(
        block_id=block_id,
        block_type=block_type,
        block_number=block_number,
        block_count=config.protocol.blocks,
        trials=tuple(trials),
    )


def compile_session_plan(config: ExperimentConfig) -> SessionPlan:
    fingerprint = config_fingerprint(config)
    items: list[PlanItem] = []
    if config.protocol.initial_rest_seconds > 0:
        items.append(RestPlan(
            rest_id="rest-initial",
            rest_type="initial",
            duration_seconds=config.protocol.initial_rest_seconds,
        ))
    practice_stimuli = _practice_stimuli(config)
    if practice_stimuli:
        items.append(_make_block(
            config, "practice-block", "practice", 0, practice_stimuli, 1
        ))
    first_trial_number = 1
    for block_number, stimuli in enumerate(_experiment_stimuli_by_block(config), start=1):
        block = _make_block(
            config,
            f"experiment-block-{block_number:03d}",
            "experiment",
            block_number,
            stimuli,
            first_trial_number,
        )
        items.append(block)
        first_trial_number += len(stimuli)
        if block_number < config.protocol.blocks and config.protocol.inter_block_break_seconds > 0:
            items.append(BreakPlan(
                break_id=f"break-after-{block_number:03d}",
                after_block=block_number,
                duration_seconds=config.protocol.inter_block_break_seconds,
            ))
    if config.protocol.final_rest_seconds > 0:
        items.append(RestPlan(
            rest_id="rest-final",
            rest_type="final",
            duration_seconds=config.protocol.final_rest_seconds,
        ))
    return SessionPlan(
        plan_id=f"plan-{fingerprint[:16]}",
        config_hash=fingerprint,
        experiment_id=config.experiment_id,
        profile=config.profile.value,
        random_seed=config.random_seed,
        items=tuple(items),
    )
