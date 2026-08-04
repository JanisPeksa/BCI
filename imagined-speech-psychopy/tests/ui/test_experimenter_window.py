from imagined_speech.ui.application import format_engine_diagnostic
from imagined_speech.ui.experimenter_window import controls_for


def test_operator_controls_follow_remote_snapshot_state() -> None:
    ready = controls_for("ready", "ready")
    assert ready.start_protocol
    assert ready.abort
    assert not ready.create_session

    running = controls_for("running", "running")
    assert running.pause
    assert running.repeat
    assert not running.resume

    paused = controls_for("running", "paused")
    assert paused.resume
    assert paused.repeat

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
