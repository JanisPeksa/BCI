from imagined_speech.ui.application import format_engine_diagnostic
from imagined_speech.ui.experimenter_window import controls_for


def test_operator_controls_follow_remote_snapshot_state() -> None:
    waiting = controls_for("created", "ready")
    assert waiting.init_subject_ui
    assert not waiting.subject_ui_initializing
    assert not waiting.start_protocol

    initializing = controls_for(
        "created", "ready", subject_initializing=True
    )
    assert not initializing.init_subject_ui
    assert initializing.subject_ui_initializing

    ready = controls_for("ready", "ready")
    assert ready.start_protocol
    assert ready.abort
    assert not ready.create_session

    running = controls_for(
        "running", "running", in_trial=True, stage_type="experiment"
    )
    assert running.pause
    assert running.repeat_trial
    assert running.repeat_block
    assert not running.resume

    paused = controls_for("running", "paused", in_trial=True)
    assert paused.resume
    assert paused.repeat_trial

    checkpoint = controls_for("running", "awaiting_experiment")
    assert checkpoint.start_experiment
    assert checkpoint.repeat_last_practice_trial
    assert checkpoint.refit

    finalized = controls_for("finalized", "completed")
    assert finalized.create_session
    assert not finalized.abort


def test_engine_diagnostic_format_highlights_control_ack_contract() -> None:
    value = format_engine_diagnostic({
        "engine_state": "awaiting_neutral",
        "pending_control": "repeat_block",
        "presentation_revision": 11,
        "expected_neutral_previous_presentation_id": "presentation-8",
        "trial_id": "trial-1",
        "attempt": 1,
    })

    assert "state=awaiting_neutral" in value
    assert "control=repeat_block" in value
    assert "expected_previous=presentation-8" in value
