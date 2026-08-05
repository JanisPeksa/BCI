"""Long-lived networked experimenter window and workflow policy."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

try:
    from PyQt6.QtCore import QProcess, QTimer
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtNetwork import QAbstractSocket, QTcpSocket
    from PyQt6.QtWidgets import (
        QApplication,
        QFileDialog,
        QInputDialog,
        QMainWindow,
        QMessageBox,
        QStackedWidget,
    )
except ModuleNotFoundError as exc:  # Pure workflow helpers remain headless-safe.
    _PYQT_IMPORT_ERROR: ModuleNotFoundError | None = exc
    QMainWindow = object  # type: ignore[assignment,misc]
else:
    _PYQT_IMPORT_ERROR = None

from imagined_speech import __version__
from imagined_speech.ipc.framing import decode_envelope, encode_envelope
from imagined_speech.ipc.messages import (
    ClientRole,
    Envelope,
    HelloPayload,
    MessageType,
    OperatorCommandPayload,
    OperatorStatePayload,
    ServiceStatePayload,
    SessionFinalizedPayload,
    SessionReadyPayload,
    message,
)
from imagined_speech.runtime.commands import (
    OperatorCommand,
    OperatorCommandRecord,
    OperatorCommandStatus,
)
if _PYQT_IMPORT_ERROR is None:
    from imagined_speech.ui.monitoring_workspace import DEFAULT_LAYOUT_ID
    from imagined_speech.ui.protocol_page import ProtocolControlPage
    from imagined_speech.ui.settings import (
        SETUP_SCHEMA_VERSION,
        WORKSPACE_SCHEMA_VERSION,
        ExperimenterSettingsStore,
    )
    from imagined_speech.ui.setup_page import SessionSetupPage


class ExperimenterWorkflowState(StrEnum):
    SETUP = "setup"
    CONNECTING = "connecting"
    READY = "ready"
    PROTOCOL_ACTIVE = "protocol_active"
    FINALIZING = "finalizing"
    REVIEW = "review"


@dataclass(frozen=True)
class OperatorControlState:
    create_session: bool
    init_subject_ui: bool
    subject_ui_initializing: bool
    start_protocol: bool
    pause: bool
    resume: bool
    repeat: bool
    refit: bool
    abort: bool


def controls_for(
    runtime_state: str | None,
    engine_state: str | None,
    *,
    in_trial: bool = False,
    subject_connected: bool = False,
    subject_initializing: bool = False,
) -> OperatorControlState:
    terminal = runtime_state in {"finalized", "failed"}
    no_session = runtime_state is None or terminal
    active = runtime_state is not None and not terminal
    return OperatorControlState(
        create_session=no_session,
        init_subject_ui=(
            runtime_state in {"created", "connecting"}
            and not subject_connected
            and not subject_initializing
        ),
        subject_ui_initializing=(
            runtime_state in {"created", "connecting"}
            and (subject_connected or subject_initializing)
        ),
        start_protocol=runtime_state == "ready" and engine_state == "ready",
        pause=engine_state == "running",
        resume=engine_state == "paused",
        repeat=engine_state in {"running", "paused"} and in_trial,
        refit=engine_state in {"running", "paused"},
        abort=active,
    )


def format_engine_diagnostic(value: dict | None) -> str:
    if not value:
        return "unavailable"
    parts = [f"state={value.get('engine_state')}"]
    for key, label in (
        ("pending_control", "control"),
        ("presentation_revision", "revision"),
        ("presentation_id", "presentation"),
        ("expected_neutral_previous_presentation_id", "expected_previous"),
        ("current_screen", "screen"),
        ("block_id", "block"),
        ("trial_id", "trial"),
        ("attempt", "attempt"),
        ("failure_reason", "failure"),
    ):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label}={item}")
    return ", ".join(parts)


class ExperimenterWindow(QMainWindow):
    def __init__(
        self,
        host: str,
        port: int,
        config_path: Path,
        participant: str,
        session_label: str | None = None,
        output_root: Path | None = None,
        *,
        settings_store: ExperimenterSettingsStore | None = None,
        auto_connect: bool = True,
    ) -> None:
        if _PYQT_IMPORT_ERROR is not None:
            raise RuntimeError(
                "PyQt6 is required to construct ExperimenterWindow"
            ) from _PYQT_IMPORT_ERROR
        super().__init__()
        self.setWindowTitle("Imagined Speech - Experimenter")
        self.resize(1440, 900)
        self.host = host
        self.port = port
        self.settings = settings_store or ExperimenterSettingsStore()
        self.workflow_state = ExperimenterWorkflowState.SETUP
        self.current_session_id: str | None = None
        self.latest_state: OperatorStatePayload | None = None
        self.review_summary: SessionFinalizedPayload | None = None
        self._finalized_session_ids: set[str] = set()
        self._service_state: ServiceStatePayload | None = None
        self._closing = False
        self._closing_committed = False
        self._close_after_finalize = False
        self._subject_launch_requested = False

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.setup_page = SessionSetupPage(
            config_path, participant, session_label, output_root
        )
        self.protocol_page = ProtocolControlPage()
        self.pages.addWidget(self.setup_page)
        self.pages.addWidget(self.protocol_page)
        self.setup_page.createRequested.connect(self._create_session)
        self.setup_page.resetRequested.connect(self._reset_setup)
        self.protocol_page.commandRequested.connect(self._command)
        self.protocol_page.initSubjectRequested.connect(self._init_subject_ui)
        self.protocol_page.newSessionRequested.connect(self._new_session)
        self.protocol_page.exportRequested.connect(self._export_summary)
        self.protocol_page.resetLayoutRequested.connect(self._reset_workspace)

        self.workspace_save_timer = QTimer(self)
        self.workspace_save_timer.setSingleShot(True)
        self.workspace_save_timer.setInterval(350)
        self.workspace_save_timer.timeout.connect(self._save_workspace_settings)
        self.protocol_page.workspace.stateChanged.connect(
            self.workspace_save_timer.start
        )

        self.subject_process = QProcess(self)
        self.subject_process.setProgram(sys.executable)
        self.subject_process.setArguments([
            "-m",
            "imagined_speech.cli",
            "_subject-client",
            "--host",
            host,
            "--port",
            str(port),
        ])
        self.subject_process.setProcessChannelMode(
            QProcess.ProcessChannelMode.ForwardedChannels
        )
        self.subject_process.started.connect(self._subject_process_started)
        self.subject_process.errorOccurred.connect(self._subject_process_error)
        self.subject_process.finished.connect(self._subject_process_finished)

        self._restore_window_geometry()
        self._restore_workspace_settings()
        self._restore_setup_settings()
        self._set_workflow_state(ExperimenterWorkflowState.SETUP)

        self.socket = QTcpSocket(self)
        self.socket.connected.connect(self._connected)
        self.socket.readyRead.connect(self._read)
        self.socket.errorOccurred.connect(self._socket_error)
        if auto_connect:
            self.socket.connectToHost(host, port)

    def _send(self, envelope: Envelope) -> None:
        self.socket.write(encode_envelope(envelope))

    def _connected(self) -> None:
        self._send(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.OPERATOR,
                software_version=__version__,
                process_id=os.getpid(),
            ),
        ))

    def _read(self) -> None:
        while self.socket.canReadLine():
            try:
                self._handle_message(
                    decode_envelope(bytes(self.socket.readLine()))
                )
            except Exception as exc:
                self.protocol_page.show_alert(
                    f"Invalid backend message: {exc}", error=True
                )

    def _handle_message(self, envelope: Envelope) -> None:
        if envelope.type == MessageType.HELLO_ACCEPTED:
            self.setup_page.set_service_state(
                "Backend ready. The subject UI will launch after session setup.",
                True,
            )
        elif envelope.type == MessageType.SERVICE_STATE:
            state = ServiceStatePayload.model_validate(envelope.payload)
            self._service_state = state
            if state.subject_connected:
                self._subject_launch_requested = False
            text = (
                "Waiting for the previous subject UI process to exit."
                if state.subject_connected and state.active_session_id is None
                else (
                    "PsychoPy subject UI connected."
                    if state.subject_connected
                    else "Backend ready. The subject UI has not been launched."
                )
            )
            self.setup_page.set_service_state(text, state.can_create_session)
            self._render_controls()
        elif envelope.type == MessageType.SESSION_READY:
            SessionReadyPayload.model_validate(envelope.payload)
            self.current_session_id = envelope.session_id
            self._subject_launch_requested = False
            self._set_workflow_state(ExperimenterWorkflowState.READY)
            self.protocol_page.set_controls(controls_for(
                "ready",
                "ready",
                subject_connected=True,
            ))
        elif envelope.type == MessageType.OPERATOR_STATE:
            state = OperatorStatePayload.model_validate(envelope.payload)
            if state.runtime_state in {"finalized", "failed"}:
                if state.session_id in self._finalized_session_ids:
                    return
                # SESSION_FINALIZED is the authoritative terminal transition.
                # Until it arrives, keep the old session non-actionable and
                # wait for its validation/finalization summary.
                self.latest_state = state
                self.current_session_id = state.session_id
                self.protocol_page.render(state)
                self._set_workflow_state(ExperimenterWorkflowState.FINALIZING)
                self.protocol_page.set_controls(None)
                return
            self.latest_state = state
            self.current_session_id = state.session_id
            self.protocol_page.render(state)
            self._synchronize_workflow(state)
            self._render_controls()
        elif envelope.type == MessageType.OPERATOR_COMMAND_RESULT:
            record = OperatorCommandRecord.model_validate(envelope.payload)
            if record.status == OperatorCommandStatus.REJECTED:
                self.protocol_page.show_alert(
                    f"{record.command.value} rejected: {record.reason}", error=True
                )
        elif envelope.type == MessageType.SESSION_FINALIZED:
            summary = SessionFinalizedPayload.model_validate(envelope.payload)
            self.review_summary = summary
            self._finalized_session_ids.add(summary.session_id)
            self.current_session_id = None
            self.protocol_page.render_finalized(summary)
            self._set_workflow_state(ExperimenterWorkflowState.REVIEW)
            self.protocol_page.set_controls(
                controls_for(summary.state, summary.validation.status if summary.validation else None)
            )
            if self._close_after_finalize:
                self._commit_close()
        elif envelope.type == MessageType.ERROR:
            error = str(envelope.payload.get("error") or "unknown backend error")
            diagnostics = envelope.payload.get("engine")
            if diagnostics:
                error += "\n" + format_engine_diagnostic(diagnostics)
            if (
                self.workflow_state == ExperimenterWorkflowState.CONNECTING
                and self.current_session_id is None
            ):
                self._set_workflow_state(ExperimenterWorkflowState.SETUP)
                self.setup_page.set_busy(False)
                self.setup_page.warning_label.setText(error)
                self.setup_page.warning_label.setStyleSheet("color: #b00020;")
            else:
                self.protocol_page.show_alert(error, error=True)
        elif envelope.type == MessageType.SHUTDOWN:
            self._closing_committed = True
            self.close()

    def _create_session(self) -> None:
        if self.workflow_state != ExperimenterWorkflowState.SETUP:
            return
        try:
            payload = self.setup_page.create_payload()
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start session", str(exc))
            return
        self.latest_state = None
        self.review_summary = None
        self.protocol_page.clear()
        self.protocol_page.show_connecting()
        self.setup_page.set_busy(True)
        self._save_setup_settings()
        self._set_workflow_state(ExperimenterWorkflowState.CONNECTING)
        self._send(message(MessageType.CREATE_SESSION, payload))

    def _render_controls(self) -> None:
        if self.workflow_state == ExperimenterWorkflowState.SETUP:
            return
        if self.workflow_state == ExperimenterWorkflowState.REVIEW:
            self.protocol_page.set_controls(
                controls_for("finalized", "completed")
            )
            return
        if self.workflow_state == ExperimenterWorkflowState.FINALIZING:
            self.protocol_page.set_controls(None)
            return
        if self.workflow_state == ExperimenterWorkflowState.READY:
            self.protocol_page.set_controls(controls_for(
                "ready",
                "ready",
                subject_connected=True,
            ))
            return
        state = self.latest_state
        if state is None:
            return
        self.protocol_page.set_controls(controls_for(
            state.runtime_state,
            state.engine_state,
            in_trial=state.view_state.trial_number is not None,
            subject_connected=bool(
                self._service_state and self._service_state.subject_connected
            ),
            subject_initializing=self._subject_launch_requested,
        ))

    def _init_subject_ui(self) -> None:
        if self.current_session_id is None:
            return
        if self._service_state and self._service_state.subject_connected:
            return
        if self.subject_process.state() != QProcess.ProcessState.NotRunning:
            return
        self._subject_launch_requested = True
        self.protocol_page.phase_label.setText(
            "Phase: launching PsychoPy subject UI"
        )
        if self.current_session_id is not None:
            self._render_controls()
        self.subject_process.start()

    def _subject_process_started(self) -> None:
        self.protocol_page.phase_label.setText(
            "Phase: initializing subject UI and timing preflight"
        )
        self._render_controls()

    def _subject_process_error(self, _error) -> None:  # type: ignore[no-untyped-def]
        self._subject_launch_requested = False
        self.protocol_page.show_alert(
            f"Could not launch subject UI: {self.subject_process.errorString()}",
            error=True,
        )
        self._render_controls()

    def _subject_process_finished(
        self, exit_code: int, _status
    ) -> None:  # type: ignore[no-untyped-def]
        self._subject_launch_requested = False
        if (
            not self._closing
            and self.current_session_id is not None
            and self.workflow_state != ExperimenterWorkflowState.FINALIZING
        ):
            self.protocol_page.show_alert(
                f"Subject UI exited before session finalization (code {exit_code}).",
                error=True,
            )
        self._render_controls()

    def _synchronize_workflow(self, state: OperatorStatePayload) -> None:
        target = {
            "created": ExperimenterWorkflowState.CONNECTING,
            "connecting": ExperimenterWorkflowState.CONNECTING,
            "ready": ExperimenterWorkflowState.READY,
            "pre_roll": ExperimenterWorkflowState.PROTOCOL_ACTIVE,
            "running": ExperimenterWorkflowState.PROTOCOL_ACTIVE,
            "post_roll": ExperimenterWorkflowState.FINALIZING,
            "finalized": ExperimenterWorkflowState.REVIEW,
            "failed": ExperimenterWorkflowState.REVIEW,
        }.get(state.runtime_state, ExperimenterWorkflowState.CONNECTING)
        self._set_workflow_state(target)

    def _set_workflow_state(self, state: ExperimenterWorkflowState) -> None:
        self.workflow_state = state
        self.pages.setCurrentWidget(
            self.setup_page
            if state == ExperimenterWorkflowState.SETUP
            else self.protocol_page
        )
        if state == ExperimenterWorkflowState.REVIEW:
            self.protocol_page.set_controls(controls_for("finalized", "completed"))

    def _command(self, command_value: str) -> None:
        if self.current_session_id is None:
            return
        command = OperatorCommand(command_value)
        note: str | None = None
        if command == OperatorCommand.REFIT:
            note, accepted = QInputDialog.getText(
                self,
                "Electrode adjustment",
                "Describe the headset/electrode adjustment:",
            )
            if not accepted:
                return
            note = note.strip()
            if not note:
                QMessageBox.warning(self, "Note required", "Enter an adjustment note.")
                return
        if command == OperatorCommand.ABORT:
            answer = QMessageBox.question(
                self,
                "Abort session",
                "Abort this session and finalize the partial package?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._send(message(
            MessageType.OPERATOR_COMMAND,
            OperatorCommandPayload(command=command.value, note=note),
            session_id=self.current_session_id,
        ))

    def _new_session(self) -> None:
        if self.workflow_state != ExperimenterWorkflowState.REVIEW:
            return
        self.latest_state = None
        self.review_summary = None
        self.current_session_id = None
        self.protocol_page.clear()
        self._set_workflow_state(ExperimenterWorkflowState.SETUP)
        can_create = bool(self._service_state and self._service_state.can_create_session)
        self.setup_page.set_busy(False)
        self.setup_page.set_service_state(
            "Backend ready. The subject UI will launch after session setup."
            if can_create
            else "Waiting for the previous subject UI process to exit.",
            can_create,
        )

    def _export_summary(self) -> None:
        if self.review_summary is None:
            return
        session_path = Path(self.review_summary.session_path)
        default = session_path.parent / f"{session_path.name}-summary.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export session summary", str(default), "JSON (*.json)"
        )
        if path:
            Path(path).write_text(
                self.review_summary.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )

    def _restore_setup_settings(self) -> None:
        version = self.settings.int_value("schema/version")
        if version not in {None, SETUP_SCHEMA_VERSION}:
            self.setup_page.warning_label.setText(
                f"Saved setup schema {version} is unsupported; defaults were used."
            )
            return
        saved_config = str(self.settings.value("setup/config_path", "")).strip()
        if saved_config and Path(saved_config).is_file():
            self.setup_page.config_edit.setText(str(Path(saved_config).resolve()))
            self.setup_page.load_configuration()
        for key, edit in (
            ("setup/participant_id", self.setup_page.participant_edit),
            ("setup/session_label", self.setup_page.session_label_edit),
            ("setup/output_root", self.setup_page.output_edit),
        ):
            value = str(self.settings.value(key, "")).strip()
            if value:
                edit.setText(value)
        saved_device = str(self.settings.value("setup/device_path", "")).strip()
        if saved_device and Path(saved_device).is_file():
            self.setup_page.device_edit.setText(str(Path(saved_device).resolve()))
        if saved_config:
            seed = self.settings.int_value("setup/random_seed")
            if seed is not None and seed >= 0:
                self.setup_page.seed_spin.setValue(seed)
        self._restore_screen(self.settings.value("setup/subject_screen"))
        mode = str(self.settings.value("setup/subject_window_mode", ""))
        mode_index = self.setup_page.window_mode_combo.findData(mode)
        if mode_index >= 0:
            self.setup_page.window_mode_combo.setCurrentIndex(mode_index)
        saved_context = str(self.settings.value("setup/audio_context", ""))
        ready = str(self.settings.value("setup/audio_ready", "false")).lower() in {
            "1", "true", "yes", "on"
        }
        if ready and saved_context == self.setup_page.audio_context():
            self.setup_page.audio_ready.setChecked(True)
            self.setup_page._audio_context_for_confirmation = saved_context
        self.setup_page.refresh_preview()

    def _save_setup_settings(self) -> bool:
        try:
            payload = self.setup_page.create_payload()
        except Exception:
            return False
        values = {
            "schema/version": SETUP_SCHEMA_VERSION,
            "setup/participant_id": payload.participant_id,
            "setup/session_label": payload.session_label or "",
            "setup/config_path": payload.config_path,
            "setup/device_path": payload.device_profile_path or "",
            "setup/output_root": payload.output_root or "",
            "setup/random_seed": payload.random_seed or 0,
            "setup/subject_screen": self._screen_identity(),
            "setup/subject_window_mode": payload.window_mode.value if payload.window_mode else "",
            "setup/audio_ready": self.setup_page.audio_ready.isChecked(),
            "setup/audio_context": self.setup_page._audio_context_for_confirmation or "",
        }
        for key, value in values.items():
            self.settings.set_value(key, value)
        self.settings.sync()
        return True

    def _reset_setup(self) -> None:
        self.settings.clear_setup()
        self.setup_page.reset_defaults()

    def _screen_identity(self) -> str:
        index = self.setup_page.subject_screen_combo.currentData()
        if index is None:
            return ""
        screens = QApplication.screens()
        name = screens[index].name() if 0 <= index < len(screens) else ""
        return json.dumps({"name": name, "index": index}, separators=(",", ":"))

    def _restore_screen(self, value: object) -> None:
        if not value:
            return
        try:
            identity = json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return
        screens = QApplication.screens()
        for index, screen in enumerate(screens):
            if identity.get("name") and screen.name() == identity["name"]:
                combo_index = self.setup_page.subject_screen_combo.findData(index)
                self.setup_page.subject_screen_combo.setCurrentIndex(combo_index)
                return
        fallback = int(identity.get("index", 0))
        combo_index = self.setup_page.subject_screen_combo.findData(fallback)
        if combo_index >= 0:
            self.setup_page.subject_screen_combo.setCurrentIndex(combo_index)

    def _restore_workspace_settings(self) -> None:
        version = self.settings.int_value("workspace/schema_version")
        if version != WORKSPACE_SCHEMA_VERSION:
            self.protocol_page.workspace.reset()
            return
        try:
            assignments = json.loads(
                str(self.settings.value("workspace/pane_assignments", "{}"))
            )
            sizes = json.loads(
                str(self.settings.value("workspace/splitter_sizes", "{}"))
            )
        except json.JSONDecodeError:
            assignments, sizes = {}, {}
        self.protocol_page.workspace.restore_preferences(
            self.settings.value("workspace/layout_id", DEFAULT_LAYOUT_ID),
            assignments,
            sizes,
        )
        self.protocol_page.layout_picker.set_current(
            self.protocol_page.workspace.current_layout_id
        )

    def _save_workspace_settings(self) -> None:
        layout_id, assignments, sizes = self.protocol_page.workspace.preferences()
        self.settings.set_value("workspace/schema_version", WORKSPACE_SCHEMA_VERSION)
        self.settings.set_value("workspace/layout_id", layout_id)
        self.settings.set_value(
            "workspace/pane_assignments", json.dumps(assignments, separators=(",", ":"))
        )
        self.settings.set_value(
            "workspace/splitter_sizes", json.dumps(sizes, separators=(",", ":"))
        )
        self.settings.sync()

    def _reset_workspace(self) -> None:
        self.protocol_page.workspace.reset()
        self.protocol_page.layout_picker.set_current(DEFAULT_LAYOUT_ID)
        self.settings.clear_workspace()
        self._save_workspace_settings()

    def _restore_window_geometry(self) -> None:
        geometry = self.settings.byte_array("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        screens = QApplication.screens()
        if screens and not any(
            self.frameGeometry().intersects(screen.availableGeometry())
            for screen in screens
        ):
            self.move(screens[0].availableGeometry().topLeft())

    def _save_window_geometry(self) -> None:
        self.settings.set_value("window/geometry", self.saveGeometry())
        self.settings.sync()

    def _socket_error(self, _error) -> None:  # type: ignore[no-untyped-def]
        if not self._closing:
            self.setup_page.set_service_state(
                f"Backend connection error: {self.socket.errorString()}", False
            )

    def _commit_close(self) -> None:
        if self._closing_committed:
            return
        self._closing = True
        self._closing_committed = True
        self._save_setup_settings()
        self._save_workspace_settings()
        self._save_window_geometry()
        self._send(message(MessageType.SHUTDOWN))
        self.socket.flush()
        self.socket.disconnectFromHost()
        if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self.socket.waitForDisconnected(500)
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_committed:
            event.accept()
            return
        if self.current_session_id is not None:
            answer = QMessageBox.question(
                self,
                "Close experimenter",
                (
                    "Close after the current session finishes finalizing?"
                    if self.workflow_state == ExperimenterWorkflowState.FINALIZING
                    else "Close the application and finalize the current recording?"
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._close_after_finalize = True
            if self.workflow_state != ExperimenterWorkflowState.FINALIZING:
                self._send(message(
                    MessageType.OPERATOR_COMMAND,
                    OperatorCommandPayload(
                        command=OperatorCommand.ABORT.value,
                        note="Experimenter application closed",
                    ),
                    session_id=self.current_session_id,
                ))
            event.ignore()
            return
        self._closing = True
        self._closing_committed = True
        self._save_setup_settings()
        self._save_workspace_settings()
        self._save_window_geometry()
        self._send(message(MessageType.SHUTDOWN))
        self.socket.flush()
        self.socket.disconnectFromHost()
        if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self.socket.waitForDisconnected(500)
        event.accept()
