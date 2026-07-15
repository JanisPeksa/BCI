from collections import Counter
from pathlib import Path

from imagined_speech.config import load_experiment
from imagined_speech.plan import BreakPlan, RestPlan, compile_session_plan


RESOURCE_ROOT = Path(__file__).parents[1] / "imagined_speech" / "resources"


def test_plan_is_deterministic_and_matches_projected_duration() -> None:
    resolved = load_experiment(RESOURCE_ROOT / "configs" / "imagined_only.yaml")

    first = compile_session_plan(resolved.config)
    second = compile_session_plan(resolved.config)

    assert first == second
    assert first.experiment_trial_count == 160
    assert first.practice_trial_count == 4
    assert first.total_duration_seconds == resolved.config.projected_duration_seconds
    assert sum(isinstance(item, BreakPlan) for item in first.items) == 3
    assert sum(isinstance(item, RestPlan) for item in first.items) == 2


def test_seed_changes_order_without_changing_balance() -> None:
    resolved = load_experiment(RESOURCE_ROOT / "configs" / "imagined_only.yaml")
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
    assert set(Counter(original_order).values()) == {10}


def test_stimuli_are_evenly_distributed_across_blocks() -> None:
    resolved = load_experiment(RESOURCE_ROOT / "configs" / "feis_comparable.yaml")
    plan = compile_session_plan(resolved.config)

    assert [len(block.trials) for block in plan.experiment_blocks] == [40, 40, 40, 40]
    for stimulus in resolved.config.stimuli:
        block_counts = [
            sum(trial.stimulus_id == stimulus.id for trial in block.trials)
            for block in plan.experiment_blocks
        ]
        assert max(block_counts) - min(block_counts) <= 1
