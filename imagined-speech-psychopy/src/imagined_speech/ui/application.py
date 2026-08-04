"""Networked PyQt6 operator console for the local backend service."""

from __future__ import annotations

import os
from pathlib import Path

from imagined_speech import __version__
from imagined_speech.ipc.framing import decode_envelope, encode_envelope
from imagined_speech.ipc.messages import (
    ClientRole,
    CreateSessionPayload,
    HelloPayload,
    MessageType,
    OperatorCommandPayload,
    message,
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


def run_operator_application(
    host: str,
    port: int,
    config_path: Path,
    participant: str,
    session_label: str | None = None,
    output_root: Path | None = None,
) -> int:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIntValidator
    from PyQt6.QtNetwork import QAbstractSocket, QTcpSocket
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QPlainTextEdit,
        QVBoxLayout,
        QWidget,
    )

    class OperatorWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Imagined Speech — Operator")
            self.resize(900, 650)
            self.socket = QTcpSocket(self)
            self.socket.connected.connect(self._connected)
            self.socket.readyRead.connect(self._read)
            self.socket.errorOccurred.connect(self._socket_error)
            self.current_session_id: str | None = None
            self._closing = False
            self._shown_warnings: set[str] = set()
            self._shown_failure: str | None = None

            root = QWidget()
            layout = QVBoxLayout(root)
            form = QFormLayout()
            self.config_edit = QLineEdit(str(config_path))
            self.participant_edit = QLineEdit(participant)
            self.label_edit = QLineEdit(session_label or "")
            self.output_edit = QLineEdit(str(output_root) if output_root else "")
            self.screen_edit = QLineEdit("")
            self.screen_edit.setValidator(QIntValidator(0, 32, self.screen_edit))
            self.screen_edit.setPlaceholderText("Use session configuration")
            self.window_mode = QComboBox()
            self.window_mode.addItems([
                "Use session configuration",
                "FULL_SCREEN",
                "PREVIOUS_POSITION",
                "TOP_LEFT",
                "CENTER",
            ])
            form.addRow("Configuration", self.config_edit)
            form.addRow("Participant", self.participant_edit)
            form.addRow("Session label", self.label_edit)
            form.addRow("Output root", self.output_edit)
            form.addRow("Subject screen override", self.screen_edit)
            form.addRow("Subject window override", self.window_mode)
            layout.addLayout(form)

            self.status = QLabel("Connecting to backend…")
            self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.status)
            controls = QHBoxLayout()
            self.create_button = QPushButton("Connect and start recording")
            self.create_button.setEnabled(False)
            self.create_button.clicked.connect(self._create_session)
            controls.addWidget(self.create_button)
            self.buttons: dict[str, QPushButton] = {}
            for command, title in (
                ("start_protocol", "Start protocol"),
                ("pause", "Pause"),
                ("resume", "Resume"),
                ("repeat_trial", "Repeat trial"),
                ("repeat_block", "Repeat block"),
                ("abort", "Abort"),
            ):
                button = QPushButton(title)
                button.setEnabled(False)
                button.clicked.connect(
                    lambda _checked=False, value=command: self._command(value)
                )
                controls.addWidget(button)
                self.buttons[command] = button
            layout.addLayout(controls)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log, 1)
            self.setCentralWidget(root)
            self.socket.connectToHost(host, port)

        def _send(self, envelope) -> None:  # type: ignore[no-untyped-def]
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
                envelope = decode_envelope(bytes(self.socket.readLine()))
                if envelope.type == MessageType.HELLO_ACCEPTED:
                    self.status.setText("Backend connected; waiting for PsychoPy subject client")
                    self.create_button.setEnabled(True)
                elif envelope.type == MessageType.SESSION_READY:
                    self.current_session_id = envelope.session_id
                    self.status.setText(
                        f"Recording ready: {envelope.payload.get('session_path')}"
                    )
                    self.create_button.setEnabled(False)
                    self.buttons["start_protocol"].setEnabled(True)
                    self.buttons["abort"].setEnabled(True)
                elif envelope.type == MessageType.OPERATOR_STATE:
                    self.current_session_id = envelope.session_id
                    state = envelope.payload
                    for warning in state.get("timing_warnings", []):
                        if warning not in self._shown_warnings:
                            self._shown_warnings.add(warning)
                            self.log.appendPlainText(f"Timing warning: {warning}")
                    failure = state.get("error")
                    if failure and failure != self._shown_failure:
                        self._shown_failure = str(failure)
                        self.log.appendPlainText(f"Protocol failure: {failure}")
                        self.log.appendPlainText(
                            "  engine: "
                            + format_engine_diagnostic(
                                state.get("engine_diagnostics")
                            )
                        )
                    self.status.setText(
                        f"Runtime: {state['runtime_state']} · Protocol: {state['engine_state']}"
                    )
                    engine = state["engine_state"]
                    self.buttons["pause"].setEnabled(engine == "running")
                    self.buttons["resume"].setEnabled(engine == "paused")
                    repeat_enabled = engine in {"running", "paused"}
                    self.buttons["repeat_trial"].setEnabled(repeat_enabled)
                    self.buttons["repeat_block"].setEnabled(repeat_enabled)
                    self.buttons["abort"].setEnabled(
                        state["runtime_state"] not in {"finalized", "failed"}
                    )
                elif envelope.type == MessageType.OPERATOR_COMMAND_RESULT:
                    self.log.appendPlainText(
                        f"{envelope.payload.get('command')}: {envelope.payload.get('status')} — "
                        f"{envelope.payload.get('reason')}"
                    )
                    details = envelope.payload.get("payload") or {}
                    if details.get("runtime_state_before") is not None:
                        self.log.appendPlainText(
                            "  runtime: "
                            f"{details.get('runtime_state_before')} -> "
                            f"{details.get('runtime_state_after')}"
                        )
                    self.log.appendPlainText(
                        "  before: "
                        + format_engine_diagnostic(details.get("engine_before"))
                    )
                    self.log.appendPlainText(
                        "  after:  "
                        + format_engine_diagnostic(details.get("engine_after"))
                    )
                    if envelope.payload.get("command") == "start_protocol":
                        self.buttons["start_protocol"].setEnabled(False)
                elif envelope.type == MessageType.SESSION_FINALIZED:
                    self.status.setText(
                        f"Session {envelope.payload.get('state')}: "
                        f"{envelope.payload.get('session_path')}"
                    )
                    self.current_session_id = None
                    if envelope.payload.get("error"):
                        self.log.appendPlainText(
                            f"Finalization error: {envelope.payload.get('error')}"
                        )
                        self.log.appendPlainText(
                            "  engine: "
                            + format_engine_diagnostic(
                                envelope.payload.get("engine")
                            )
                        )
                    self._shown_warnings.clear()
                    self._shown_failure = None
                    self.create_button.setEnabled(True)
                    for button in self.buttons.values():
                        button.setEnabled(False)
                elif envelope.type == MessageType.ERROR:
                    kind = envelope.payload.get("kind") or "backend"
                    self.log.appendPlainText(
                        f"Error [{kind}]: {envelope.payload.get('error')}"
                    )
                    if kind == "frame_acknowledgement_rejected":
                        self.log.appendPlainText(
                            "  received: "
                            f"revision={envelope.payload.get('received_revision')}, "
                            "previous="
                            f"{envelope.payload.get('received_previous_presentation_id')}, "
                            f"presentation={envelope.payload.get('received_presentation_id')}, "
                            f"neutral={envelope.payload.get('received_neutral')}"
                        )
                        self.log.appendPlainText(
                            "  expected: "
                            + format_engine_diagnostic(
                                envelope.payload.get("engine")
                            )
                        )
                    if self.current_session_id is None:
                        self.create_button.setEnabled(True)

        def _create_session(self) -> None:
            output = self.output_edit.text().strip()
            screen_text = self.screen_edit.text().strip()
            mode = self.window_mode.currentText()
            self._send(message(
                MessageType.CREATE_SESSION,
                CreateSessionPayload(
                    config_path=self.config_edit.text().strip(),
                    participant_id=self.participant_edit.text().strip(),
                    session_label=self.label_edit.text().strip() or None,
                    output_root=output or None,
                    screen_index=int(screen_text) if screen_text else None,
                    window_mode=(
                        None if mode == "Use session configuration" else mode
                    ),
                ),
            ))
            self.create_button.setEnabled(False)
            self.status.setText("Initializing PsychoPy and running timing preflight…")

        def _command(self, command: str) -> None:
            if self.current_session_id is None:
                return
            self.log.appendPlainText(f"{command}: sent")
            self._send(message(
                MessageType.OPERATOR_COMMAND,
                OperatorCommandPayload(command=command),
                session_id=self.current_session_id,
            ))

        def _socket_error(self, _error) -> None:  # type: ignore[no-untyped-def]
            if not self._closing:
                self.status.setText(f"Backend connection error: {self.socket.errorString()}")

        def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
            self._closing = True
            if self.current_session_id is not None:
                self._send(message(
                    MessageType.OPERATOR_COMMAND,
                    OperatorCommandPayload(
                        command="abort", note="Operator application closed"
                    ),
                    session_id=self.current_session_id,
                ))
            self._send(message(MessageType.SHUTDOWN))
            self.socket.flush()
            self.socket.disconnectFromHost()
            if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
                self.socket.waitForDisconnected(500)
            event.accept()

    app = QApplication.instance() or QApplication([])
    window = OperatorWindow()
    window.show()
    return app.exec()
