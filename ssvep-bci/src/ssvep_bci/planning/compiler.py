from __future__ import annotations

import hashlib
import json
import random

from ssvep_bci.config.models import ExperimentConfig
from ssvep_bci.planning.models import PlanStep, SessionPlan, StepKind


def config_fingerprint(config: ExperimentConfig) -> str:
    value = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def compile_session_plan(config: ExperimentConfig) -> SessionPlan:
    protocol = config.protocol
    if config.dual_stimulus is None:
        sequence_length = len(
            protocol.stimulus_sequence or (protocol.active_stimulus_id,)
        )
        trials = [
            (stimulus_id, None, None)
            for stimulus_id in config.ordered_stimulus_ids
        ]
    else:
        dual = config.dual_stimulus
        sequence_length = len(dual.resolved_conditions)
        generator = random.Random(config.random_seed)
        trials = []
        for _ in range(protocol.repetitions):
            conditions = list(dual.resolved_conditions)
            if dual.randomize_conditions:
                generator.shuffle(conditions)
            trials.extend(
                (
                    dual.target_stimulus_id,
                    condition.target_side,
                    condition.distractor_stimulus_id,
                )
                for condition in conditions
            )
    steps: list[PlanStep] = []
    if protocol.initial_rest_seconds:
        steps.append(PlanStep(
            step_id="initial-rest",
            kind=StepKind.INITIAL_REST,
            duration_seconds=protocol.initial_rest_seconds,
        ))
    for number, (stimulus_id, target_side, distractor_id) in enumerate(
        trials, start=1
    ):
        trial_id = f"trial-{number:04d}"
        if protocol.pre_stimulus_seconds:
            steps.append(PlanStep(
                step_id=f"{trial_id}-pre",
                kind=StepKind.PRE_STIMULUS,
                duration_seconds=protocol.pre_stimulus_seconds,
                trial_id=trial_id,
                trial_number=number,
                stimulus_id=stimulus_id,
                target_side=target_side,
                distractor_stimulus_id=distractor_id,
            ))
        steps.append(PlanStep(
            step_id=f"{trial_id}-stimulus",
            kind=StepKind.STIMULUS,
            duration_seconds=protocol.stimulation_seconds,
            trial_id=trial_id,
            trial_number=number,
            stimulus_id=stimulus_id,
            presentation_id=f"presentation-{number:04d}",
            target_side=target_side,
            distractor_stimulus_id=distractor_id,
        ))
        if number < len(trials):
            completed_sequence = number % sequence_length == 0
            break_seconds = protocol.inter_trial_seconds + (
                protocol.sequence_break_seconds if completed_sequence else 0.0
            )
        else:
            break_seconds = 0.0
        if break_seconds:
            steps.append(PlanStep(
                step_id=f"{trial_id}-inter",
                kind=StepKind.INTER_TRIAL,
                duration_seconds=break_seconds,
            ))
    if protocol.final_rest_seconds:
        steps.append(PlanStep(
            step_id="final-rest",
            kind=StepKind.FINAL_REST,
            duration_seconds=protocol.final_rest_seconds,
        ))
    digest = config_fingerprint(config)
    return SessionPlan(
        plan_id=f"plan-{digest[:16]}",
        config_hash=digest,
        experiment_id=config.experiment_id,
        steps=tuple(steps),
        trial_count=len(trials),
    )
