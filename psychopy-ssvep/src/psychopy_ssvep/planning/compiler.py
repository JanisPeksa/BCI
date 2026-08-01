from __future__ import annotations

import hashlib
import json
import random
from itertools import permutations

from psychopy_ssvep.config.models import ExperimentConfig
from psychopy_ssvep.planning.models import PlanStep, SessionPlan, StepKind


def config_fingerprint(config: ExperimentConfig) -> str:
    value = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def compile_session_plan(config: ExperimentConfig) -> SessionPlan:
    protocol = config.protocol
    if config.dual_stimulus is None and config.multi_stimulus is None:
        sequence_length = len(
            protocol.stimulus_sequence or (protocol.active_stimulus_id,)
        )
        trials = [
            (stimulus_id, None, None, None, None)
            for stimulus_id in config.ordered_stimulus_ids
        ]
    elif config.dual_stimulus is not None:
        dual = config.dual_stimulus
        generator = random.Random(config.random_seed)
        if dual.fixed_positions:
            sequence_length = 1
            distractor_id = dual.distractor_stimulus_ids[0]
            trials = [
                (
                    dual.target_stimulus_id,
                    dual.fixed_target_side,
                    distractor_id,
                    None,
                    None,
                )
                for _ in range(protocol.repetitions)
            ]
        else:
            sequence_length = len(dual.resolved_conditions)
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
                        None,
                        None,
                    )
                    for condition in conditions
                )
    else:
        multi = config.multi_stimulus
        assert multi is not None
        generator = random.Random(config.random_seed)
        if multi.fixed_positions:
            sequence_length = 1
            target_index = next(
                index
                for index, position in enumerate(multi.positions)
                if position.id == multi.target_position_id
            )
            distractor_iterator = iter(multi.distractor_stimulus_ids)
            assignments = tuple(
                multi.target_stimulus_id
                if index == target_index
                else next(distractor_iterator)
                for index in range(len(multi.positions))
            )
            trials = [
                (
                    multi.target_stimulus_id,
                    None,
                    None,
                    multi.positions[target_index].id,
                    assignments,
                )
                for _ in range(protocol.repetitions)
            ]
        else:
            sequence_length = len(multi.positions)
            trials = []
            assignment_decks: dict[int, list[tuple[str, ...]]] = {}
            for target_index in range(len(multi.positions)):
                deck: list[tuple[str, ...]] = []
                while len(deck) < protocol.repetitions:
                    distractor_orders = list(
                        permutations(multi.distractor_stimulus_ids)
                    )
                    if multi.randomize_conditions:
                        generator.shuffle(distractor_orders)
                    for distractor_order in distractor_orders:
                        distractor_iterator = iter(distractor_order)
                        deck.append(
                            tuple(
                                multi.target_stimulus_id
                                if index == target_index
                                else next(distractor_iterator)
                                for index in range(len(multi.positions))
                            )
                        )
                        if len(deck) == protocol.repetitions:
                            break
                assignment_decks[target_index] = deck
            for repetition in range(protocol.repetitions):
                target_indexes = list(range(len(multi.positions)))
                if multi.randomize_conditions:
                    generator.shuffle(target_indexes)
                for target_index in target_indexes:
                    assignments = assignment_decks[target_index][repetition]
                    trials.append(
                        (
                            multi.target_stimulus_id,
                            None,
                            None,
                            multi.positions[target_index].id,
                            assignments,
                        )
                    )
    steps: list[PlanStep] = []
    if protocol.initial_rest_seconds:
        steps.append(PlanStep(
            step_id="initial-rest",
            kind=StepKind.INITIAL_REST,
            duration_seconds=protocol.initial_rest_seconds,
        ))
    for number, (
        stimulus_id,
        target_side,
        distractor_id,
        target_position_id,
        position_stimulus_ids,
    ) in enumerate(trials, start=1):
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
                target_position_id=target_position_id,
                position_stimulus_ids=position_stimulus_ids,
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
            target_position_id=target_position_id,
            position_stimulus_ids=position_stimulus_ids,
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
