import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from imagined_speech.cli import default_config_path
from imagined_speech.ipc.messages import (
    MessageType,
    OperatorStatePayload,
    SessionFinalizedPayload,
    message,
)
from imagined_speech.ui.experimenter_window import (
    ExperimenterWindow,
    ExperimenterWorkflowState,
    controls_for,
)
from imagined_speech.ui.protocol_page import ProtocolControlPage
from imagined_speech.ui.settings import ExperimenterSettingsStore
from imagined_speech.ui.setup_page import SessionSetupPage
from imagined_speech.ui.widgets import create_monitoring_registry


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _state() -> OperatorStatePayload:
    return OperatorStatePayload.model_validate({
        "runtime_state": "running",
        "engine_state": "running",
        "protocol_started": True,
        "recording": True,
        "session_path": "sessions/test",
        "session_id": "session-test",
        "participant_id": "P001",
        "session_label": "RUN001",
        "experiment_id": "smoke",
        "device_profile_id": "synthetic",
        "view_state": {
            "run_state": "running",
            "screen": "thinking",
            "step_id": "step-1",
            "headline": "Think",
            "instruction": "Imagine the word",
            "stimulus_id": "left",
            "stimulus_label": "Left",
            "remaining_seconds": 1.25,
            "duration_seconds": 2.0,
            "block_type": "experiment",
            "block_number": 1,
            "block_count": 2,
            "trial_number": 3,
            "trial_count": 10,
            "presentation_id": "presentation-1",
            "revision": 2,
        },
        "acquisition": {
            "running": True,
            "sample_count": 2500,
            "dropped_batches": 1,
            "dropped_samples": 2,
            "timestamp_discontinuities": 3,
            "read_errors": 0,
            "write_errors": 0,
            "last_health_kind": "receiving",
            "last_health_severity": "info",
            "channel_names": ["F3", "F4", "marker"],
            "recent_samples": [[1.0, 2.0, 0.0], [1.5, 1.0, 0.0]],
            "sampling_rate_hz": 250,
            "eeg_channel_indexes": [0, 1],
            "eeg_channel_labels": ["F3", "F4"],
            "raw_file_size_bytes": 1_048_576,
            "free_storage_bytes": 10_737_418_240,
        },
    })


def _finalized(state: str) -> dict[str, object]:
    return {
        "state": state,
        "session_path": "sessions/test",
        "session_id": "session-test",
        "participant_id": "P001",
        "session_label": "RUN001",
        "experiment_id": "smoke",
        "device_profile_id": "synthetic",
        "error": "frame timing failed" if state == "failed" else None,
        "engine": {"engine_state": "failed" if state == "failed" else "completed"},
        "validation": None,
    }


def test_setup_scene_validates_and_builds_complete_create_payload(
    tmp_path: Path,
) -> None:
    app = _app()
    assert app is not None
    page = SessionSetupPage(default_config_path(), "P001", "RUN001", tmp_path)
    assert not page.create_button.isEnabled()

    page.set_service_state("Ready", True)
    payload = page.create_payload()

    assert page.create_button.isEnabled()
    assert payload.participant_id == "P001"
    assert payload.session_label == "RUN001"
    assert payload.output_root == str(tmp_path.resolve())
    assert payload.device_profile_path
    assert payload.random_seed is not None


def test_protocol_scene_and_modular_widgets_render_typed_live_state() -> None:
    app = _app()
    page = ProtocolControlPage()
    page.resize(1200, 800)
    page.show()
    app.processEvents()
    state = _state()

    page.render(state)
    app.processEvents()

    assert "2500 samples" in page.recording_label.text()
    assert "trial 3/10" in page.progress_label.text()
    assert page.output_label.text().endswith("sessions/test")
    registry = create_monitoring_registry()
    modules = {descriptor.factory.__module__ for descriptor in registry.descriptors}
    assert len(registry.descriptors) == 5
    assert len(modules) == 5
    assert all(".ui.widgets." in module for module in modules)
    health = page.workspace.widgets_for("acquisition_health")
    assert len(health) == 1
    assert "1.00 MiB" in health[0].label.text()


def test_protocol_scene_displays_nonfatal_timing_warning() -> None:
    app = _app()
    page = ProtocolControlPage()
    state = _state().model_copy(update={
        "timing_warnings": (
            "Display timing warning: 2 dropped frame(s) affected 1 trial attempt(s).",
        ),
    })

    page.render(state)
    app.processEvents()

    assert page.alert_label.isVisibleTo(page)
    assert "2 dropped frame(s)" in page.alert_label.text()
    assert "#fff4ce" in page.alert_label.styleSheet()


def test_finalized_scene_retains_timing_warning() -> None:
    app = _app()
    page = ProtocolControlPage()
    summary = _finalized("finalized")
    summary["timing_warnings"] = (
        "Display timing warning: 1 dropped frame(s) affected 1 trial attempt(s).",
    )

    page.render_finalized(
        SessionFinalizedPayload.model_validate(summary)
    )
    app.processEvents()

    assert "1 dropped frame(s)" in page.alert_label.text()
    assert "#fff4ce" in page.alert_label.styleSheet()


def test_primary_button_emits_init_then_start_actions() -> None:
    app = _app()
    assert app is not None
    page = ProtocolControlPage()
    initialized: list[bool] = []
    commands: list[str] = []
    page.initSubjectRequested.connect(lambda: initialized.append(True))
    page.commandRequested.connect(commands.append)
    primary = page.command_buttons["start_protocol"]

    page.set_controls(controls_for("created", "ready"))
    primary.click()
    assert initialized == [True]
    assert commands == []

    page.set_controls(controls_for("ready", "ready", subject_connected=True))
    primary.click()
    assert initialized == [True]
    assert commands == ["start_protocol"]


def test_experimenter_window_uses_setup_then_protocol_scene(
    tmp_path: Path,
) -> None:
    app = _app()
    settings = ExperimenterSettingsStore(tmp_path / "experimenter_ui.ini")
    window = ExperimenterWindow(
        "127.0.0.1",
        9999,
        default_config_path(),
        "P001",
        settings_store=settings,
        auto_connect=False,
    )
    assert window.pages.count() == 2
    assert window.pages.currentWidget() is window.setup_page

    window._handle_message(message(
        MessageType.SERVICE_STATE,
        {
            "subject_connected": True,
            "active_session_id": None,
            "active_runtime_state": None,
            "can_create_session": True,
        },
    ))
    assert window.setup_page.create_button.isEnabled()

    state = _state()
    window._handle_message(message(
        MessageType.OPERATOR_STATE,
        state,
        session_id=state.session_id,
    ))
    app.processEvents()

    assert window.workflow_state == ExperimenterWorkflowState.PROTOCOL_ACTIVE
    assert window.pages.currentWidget() is window.protocol_page
    window._closing_committed = True
    window.close()


def test_subject_ui_is_lazy_and_primary_action_changes_when_ready(
    tmp_path: Path,
) -> None:
    app = _app()
    window = ExperimenterWindow(
        "127.0.0.1",
        43210,
        default_config_path(),
        "P001",
        settings_store=ExperimenterSettingsStore(tmp_path / "lazy.ini"),
        auto_connect=False,
    )
    window._handle_message(message(
        MessageType.SERVICE_STATE,
        {
            "subject_connected": False,
            "active_session_id": "session-test",
            "active_runtime_state": "created",
            "can_create_session": False,
        },
    ))
    waiting = _state().model_copy(update={
        "runtime_state": "created",
        "engine_state": "ready",
        "recording": False,
    })
    window._handle_message(message(
        MessageType.OPERATOR_STATE,
        waiting,
        session_id="session-test",
    ))
    app.processEvents()

    primary = window.protocol_page.command_buttons["start_protocol"]
    assert window.pages.currentWidget() is window.protocol_page
    assert window.subject_process.state() == QProcess.ProcessState.NotRunning
    assert primary.text() == "Init subject UI"
    assert primary.isEnabled()
    assert window.subject_process.program()
    assert window.subject_process.arguments()[-2:] == ["--port", "43210"]

    window._subject_launch_requested = True
    window._render_controls()
    assert primary.text() == "Initializing subject UI..."
    assert not primary.isEnabled()

    window._handle_message(message(
        MessageType.SESSION_READY,
        {"session_path": "sessions/test", "participant_id": "P001"},
        session_id="session-test",
    ))
    assert primary.text() == "Start protocol"
    assert primary.isEnabled()

    window._closing_committed = True
    window.close()


@pytest.mark.parametrize("terminal_state", ["finalized", "failed"])
def test_terminal_snapshots_cannot_reactivate_a_finished_session(
    tmp_path: Path,
    terminal_state: str,
) -> None:
    app = _app()
    window = ExperimenterWindow(
        "127.0.0.1",
        9999,
        default_config_path(),
        "P001",
        settings_store=ExperimenterSettingsStore(
            tmp_path / f"{terminal_state}.ini"
        ),
        auto_connect=False,
    )
    window._handle_message(message(
        MessageType.SERVICE_STATE,
        {
            "subject_connected": True,
            "active_session_id": None,
            "active_runtime_state": terminal_state,
            "can_create_session": True,
        },
    ))
    window._handle_message(message(
        MessageType.SESSION_FINALIZED,
        _finalized(terminal_state),
        session_id="session-test",
    ))

    terminal_snapshot = _state().model_copy(update={
        "runtime_state": terminal_state,
        "engine_state": "failed" if terminal_state == "failed" else "completed",
        "recording": False,
    })
    window._handle_message(message(
        MessageType.OPERATOR_STATE,
        terminal_snapshot,
        session_id="session-test",
    ))

    assert window.current_session_id is None
    assert window.workflow_state == ExperimenterWorkflowState.REVIEW
    assert window.protocol_page.new_session_button.isEnabled()

    window._handle_message(message(
        MessageType.SERVICE_STATE,
        {
            "subject_connected": False,
            "active_session_id": None,
            "active_runtime_state": terminal_state,
            "can_create_session": True,
        },
    ))
    window._subject_process_finished(
        0, QProcess.ExitStatus.NormalExit
    )
    assert window.protocol_page.new_session_button.isEnabled()

    window._new_session()
    window._handle_message(message(
        MessageType.OPERATOR_STATE,
        terminal_snapshot,
        session_id="session-test",
    ))

    assert window.current_session_id is None
    assert window.workflow_state == ExperimenterWorkflowState.SETUP
    assert window.pages.currentWidget() is window.setup_page

    sent = []
    window._send = sent.append  # type: ignore[method-assign]
    close_event = QCloseEvent()
    window.closeEvent(close_event)

    assert close_event.isAccepted()
    assert [envelope.type for envelope in sent] == [MessageType.SHUTDOWN]
    app.processEvents()
