from collections import Counter
from pathlib import Path

from imagined_speech.config import load_experiment
from imagined_speech.planning import BreakPlan, RestPlan, compile_session_plan


RESOURCE_ROOT = Path(__file__).parents[2] / "src" / "imagined_speech" / "resources"


def test_plan_is_deterministic_and_matches_projected_duration() -> None:
    resolved = load_experiment(
        RESOURCE_ROOT / "configs" / "cyton-four-phoneme.yaml"
    )

    first = compile_session_plan(resolved.config)
    second = compile_session_plan(resolved.config)

    assert first == second
    assert first.experiment_trial_count == 8
    assert first.practice_trial_count == 4
    assert first.total_duration_seconds == resolved.config.projected_duration_seconds
    assert first.practice is not None
    first_trial = first.practice.trials[0]
    assert len({phase.step_id for phase in first_trial.phases}) == len(
        first_trial.phases
    )
    assert sum(isinstance(item, BreakPlan) for item in first.items) == 1
    assert sum(isinstance(item, RestPlan) for item in first.items) == 2


def test_seed_changes_order_without_changing_balance() -> None:
    resolved = load_experiment(
        RESOURCE_ROOT / "configs" / "cyton-four-phoneme.yaml"
    )
    original = compile_session_plan(resolved.config)
    changed_config = resolved.config.model_copy(
        update={"random_seed": resolved.config.random_seed + 1}
    )
    changed = compile_session_plan(changed_config)

    original_order = [
        trial.stimulus_id
        for block in original.experiment_blocks
        for trial in block.trials
    ]
    changed_order = [
        trial.stimulus_id
        for block in changed.experiment_blocks
        for trial in block.trials
    ]
    assert original_order != changed_order
    assert Counter(original_order) == Counter(changed_order)
    assert set(Counter(original_order).values()) == {2}


def test_stimuli_are_evenly_distributed_across_blocks() -> None:
    resolved = load_experiment(RESOURCE_ROOT / "configs" / "cyton-four-phoneme.yaml")
    plan = compile_session_plan(resolved.config)

    assert [len(block.trials) for block in plan.experiment_blocks] == [4, 4]
    assert all(
        trial.post_trial_seconds == 1
        for group in plan.trial_groups
        for trial in group.trials
    )
    assert plan.total_duration_seconds == resolved.config.projected_duration_seconds
    for stimulus in resolved.config.stimuli:
        block_counts = [
            sum(trial.stimulus_id == stimulus.id for trial in block.trials)
            for block in plan.experiment_blocks
        ]
        assert max(block_counts) - min(block_counts) <= 1


def test_disabled_practice_is_omitted_from_the_plan() -> None:
    config = load_experiment(RESOURCE_ROOT / "configs" / "smoke.yaml").config
    disabled = config.protocol.practice.model_copy(update={
        "blocks": 0,
        "stimulus_ids": (),
        "repetitions_per_stimulus": 0,
    })
    config = config.model_copy(update={
        "protocol": config.protocol.model_copy(update={"practice": disabled})
    })

    plan = compile_session_plan(config)

    assert plan.practice is None
    assert plan.practice_trial_count == 0
    assert all(block.block_number >= 1 for block in plan.experiment_blocks)
