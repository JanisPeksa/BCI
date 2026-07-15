import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QPushButton, QToolButton

from imagined_speech.cli import default_config_path
from imagined_speech.engine import VirtualClock
from imagined_speech.experimenter_settings import ExperimenterSettingsStore
from imagined_speech.experimenter_ui import (
    ExperimenterWindow,
    ExperimenterWorkflowState,
)
from imagined_speech.monitoring_workspace import DEFAULT_LAYOUT_ID
from imagined_speech.operator import OperatorCommand
from imagined_speech.runtime import SessionRuntime, SessionRuntimeState
from imagined_speech.session import validate_session


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_setup_loads_without_session_and_outer_controls_stay_fixed(tmp_path) -> None:
    app = _app()
    window = ExperimenterWindow(
        default_config_path(),
        participant="UI001",
        settings_store=ExperimenterSettingsStore(tmp_path / "ui.ini"),
    )
    window.timer.stop()
    live_layout = window.live_page.layout()
    workspace_index = live_layout.indexOf(window.workspace)
    header_label_position = live_layout.indexOf(window.session_id_label)

    assert window.runtime is None
    assert window.start_button.isEnabled()
    assert "Projected duration" in window.preview_text.toPlainText()
    assert "F3" in window.montage_label.text()
    assert window.participant_edit.text() == "UI001"
    assert window.workspace.current_layout_id == DEFAULT_LAYOUT_ID

    window._set_workflow_state(ExperimenterWorkflowState.CONNECTING)
    window.resize(1400, 800)
    window.show()
    app.processEvents()
    command_widths = [
        button.width() for button in window.command_buttons.values()
    ]
    assert max(command_widths) - min(command_widths) <= 1
    assert min(command_widths) >= 120
    assert window.layout_button.width() >= 100
    assert window.layout_button.height() == next(
        iter(window.command_buttons.values())
    ).height()

    for layout_id in ("single", "columns_2", "top_columns_bottom", "grid_4"):
        window.workspace.set_layout(layout_id)
        assert live_layout.indexOf(window.workspace) == workspace_index
        assert live_layout.indexOf(window.session_id_label) == header_label_position
        assert window.command_buttons[OperatorCommand.ABORT].parent() is window.live_page

    abort_style = window.live_page.styleSheet()
    assert "abortCommand:enabled" in abort_style
    assert "protocolCommand" in abort_style
    assert "protocolCommand=\"true\"]:disabled" not in abort_style
    assert isinstance(window.layout_button, QPushButton)
    assert not isinstance(window.layout_button, QToolButton)
    window.close()
    app.processEvents()


def test_setup_and_workspace_round_trip_with_independent_schema(tmp_path) -> None:
    app = _app()
    settings_path = tmp_path / "experimenter_ui.ini"
    first = ExperimenterWindow(
        default_config_path(),
        settings_store=ExperimenterSettingsStore(settings_path),
    )
    first.participant_edit.setText("REMEMBERED")
    first.session_label_edit.setText("RUN042")
    first.seed_spin.setValue(4242)
    first.output_edit.setText(str(tmp_path / "sessions"))
    first.audio_ready.setChecked(True)
    assert first._save_setup_settings()
    first.workspace.set_assignment("pane_1", "operator_audit")
    first.workspace.set_layout("top_columns_bottom")
    first.workspace.resize(900, 500)
    first.show()
    app.processEvents()
    first._save_workspace_settings()
    first.close()
    app.processEvents()

    second = ExperimenterWindow(
        default_config_path(),
        settings_store=ExperimenterSettingsStore(settings_path),
    )
    assert second.participant_edit.text() == "REMEMBERED"
    assert second.session_label_edit.text() == "RUN042"
    assert second.seed_spin.value() == 4242
    assert second.audio_ready.isChecked()
    assert second.workspace.current_layout_id == "top_columns_bottom"
    assert second.workspace.assignments["pane_1"] == "operator_audit"

    second.close()
    app.processEvents()
    incompatible = ExperimenterSettingsStore(settings_path)
    incompatible.set_value("workspace/schema_version", 99)
    incompatible.sync()
    third = ExperimenterWindow(
        default_config_path(),
        settings_store=ExperimenterSettingsStore(settings_path),
    )
    assert third.participant_edit.text() == "REMEMBERED"
    assert third.workspace.current_layout_id == DEFAULT_LAYOUT_ID
    assert "workspace schema" in third.setup_warnings.text().lower()
    third.close()
    app.processEvents()


def test_invalid_saved_setup_falls_back_and_reset_does_not_touch_workspace(tmp_path) -> None:
    app = _app()
    path = tmp_path / "ui.ini"
    store = ExperimenterSettingsStore(path)
    store.set_value("schema/version", 1)
    store.set_value("setup/participant_id", "SAVED")
    store.set_value("setup/config_path", str(tmp_path / "missing-config.yaml"))
    store.set_value("setup/device_path", str(tmp_path / "missing-device.yaml"))
    store.set_value("workspace/schema_version", 1)
    store.set_value("workspace/layout_id", "single")
    store.set_value("workspace/pane_assignments", "{}")
    store.set_value("workspace/splitter_sizes", "{}")
    store.sync()

    window = ExperimenterWindow(
        default_config_path(),
        participant="DEFAULT",
        settings_store=ExperimenterSettingsStore(path),
    )
    assert window.participant_edit.text() == "SAVED"
    assert Path(window.config_edit.text()).resolve() == default_config_path().resolve()
    assert "unavailable" in window.setup_warnings.text().lower()
    assert window.workspace.current_layout_id == "single"
    window._reset_saved_setup()
    assert window.participant_edit.text() == "DEFAULT"
    assert window.workspace.current_layout_id == "single"

    window.audio_ready.setChecked(True)
    window.device_edit.setText(str(tmp_path / "another-device.yaml"))
    window._device_changed()
    assert not window.audio_ready.isChecked()
    window.close()
    app.processEvents()


def test_two_sequential_recordings_and_workspace_changes_do_not_restart_source(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    import imagined_speech.experimenter_ui as experimenter_ui

    created: list[tuple[SessionRuntime, VirtualClock]] = []

    def runtime_factory(*args, **kwargs):
        clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
        runtime = SessionRuntime(*args, **kwargs, clock=clock)
        created.append((runtime, clock))
        return runtime

    monkeypatch.setattr(experimenter_ui, "SessionRuntime", runtime_factory)
    window = ExperimenterWindow(
        default_config_path(),
        participant="FIRST",
        output_root=tmp_path,
        settings_store=ExperimenterSettingsStore(tmp_path / "ui.ini"),
    )
    window.show()
    window._start_session()
    deadline = time.monotonic() + 2
    while window.workflow_state != ExperimenterWorkflowState.READY:
        app.processEvents()
        window._tick()
        assert time.monotonic() < deadline

    first, first_clock = created[0]
    acquisition = first.acquisition
    assert first.state == SessionRuntimeState.READY
    assert first.recording and first.engine.state.value == "ready"
    assert window.subject_window is not None
    assert window.start_protocol_button.isEnabled()
    assert window.command_buttons[OperatorCommand.ABORT].isEnabled()
    assert not window.command_buttons[OperatorCommand.PAUSE].isEnabled()

    first_clock.advance(2)
    first.acquisition.capture_available()
    first.acquisition.flush_pending()
    before = first.acquisition_snapshot().sample_count
    window.workspace.set_layout("single")
    window.workspace.set_assignment("pane_1", "operator_audit")
    window.workspace.set_layout("grid_4")
    assert first.acquisition is acquisition and first.recording
    assert first.acquisition_snapshot().sample_count == before

    window._command(OperatorCommand.START_PROTOCOL)
    assert first.state == SessionRuntimeState.PRE_ROLL
    assert not window.start_protocol_button.isEnabled()
    assert window.command_buttons[OperatorCommand.ABORT].isEnabled()
    first_clock.advance(first.resolved.device.pre_roll_seconds)
    window._tick()
    assert first.engine.state.value == "running"
    first.execute(OperatorCommand.ABORT)
    first.tick()
    first_clock.advance(first.resolved.device.post_roll_seconds)
    first.tick()
    window._tick()

    assert window.workflow_state == ExperimenterWorkflowState.REVIEW
    assert window.runtime is None
    assert window.review_summary is not None
    assert window.new_session_button.isEnabled()
    assert all(not button.isEnabled() for button in window.command_buttons.values())
    first_id = first.writer.session_id
    first_path = first.session_path

    window._new_session()
    assert window.workflow_state == ExperimenterWorkflowState.SETUP
    assert window.review_summary is None
    window.participant_edit.setText("SECOND")
    window._start_session()
    deadline = time.monotonic() + 2
    while window.workflow_state != ExperimenterWorkflowState.READY:
        app.processEvents()
        window._tick()
        assert time.monotonic() < deadline
    second, second_clock = created[1]
    assert second.writer.session_id != first_id
    assert second.session_path != first_path
    second.execute(OperatorCommand.ABORT)
    second.tick()
    second_clock.advance(second.resolved.device.post_roll_seconds)
    second.tick()
    window._tick()

    assert validate_session(first_path).status == "aborted"
    assert validate_session(second.session_path).status == "aborted"
    window.close()
    app.processEvents()
