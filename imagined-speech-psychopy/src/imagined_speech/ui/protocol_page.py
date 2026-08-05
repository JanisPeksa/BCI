"""Protocol control and live monitoring scene."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from imagined_speech.ipc.messages import OperatorStatePayload, SessionFinalizedPayload
from imagined_speech.runtime.commands import OperatorCommand
from imagined_speech.ui.monitoring_workspace import MonitoringWorkspace, create_layout_menu
from imagined_speech.ui.widgets import create_monitoring_registry


class ProtocolControlPage(QWidget):
    commandRequested = pyqtSignal(str)
    initSubjectRequested = pyqtSignal()
    newSessionRequested = pyqtSignal()
    exportRequested = pyqtSignal()
    resetLayoutRequested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        header = QGridLayout()
        self.session_label = QLabel("Session: -")
        self.runtime_label = QLabel("Runtime: -")
        self.recording_label = QLabel("Recording: -")
        self.protocol_label = QLabel("Protocol: -")
        self.phase_label = QLabel("Phase: -")
        self.progress_label = QLabel("Progress: -")
        for widget in (
            self.session_label,
            self.runtime_label,
            self.recording_label,
            self.protocol_label,
            self.phase_label,
            self.progress_label,
        ):
            widget.setStyleSheet("font-size: 16px;")
        header.addWidget(self.session_label, 0, 0, 1, 2)
        header.addWidget(self.runtime_label, 1, 0)
        header.addWidget(self.recording_label, 1, 1)
        header.addWidget(self.protocol_label, 2, 0)
        header.addWidget(self.phase_label, 2, 1)
        header.addWidget(self.progress_label, 3, 0, 1, 2)
        root.addLayout(header)

        self.alert_label = QLabel()
        self.alert_label.setWordWrap(True)
        self.alert_label.hide()
        root.addWidget(self.alert_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.command_buttons: dict[str, QPushButton] = {}
        self.primary_action = OperatorCommand.START_PROTOCOL.value
        for command, label in (
            (OperatorCommand.START_PROTOCOL, "Init subject UI"),
            (OperatorCommand.PAUSE, "Pause"),
            (OperatorCommand.RESUME, "Resume"),
            (OperatorCommand.REPEAT_TRIAL, "Repeat trial"),
            (OperatorCommand.REPEAT_BLOCK, "Repeat block"),
            (
                OperatorCommand.REPEAT_LAST_PRACTICE_TRIAL,
                "Repeat last practice trial",
            ),
            (OperatorCommand.REFIT, "Electrode adjustment..."),
            (OperatorCommand.ABORT, "Abort"),
        ):
            button = QPushButton(label)
            button.setProperty("protocolCommand", True)
            button.setMinimumHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            if command == OperatorCommand.START_PROTOCOL:
                button.clicked.connect(self._primary_clicked)
            else:
                button.clicked.connect(
                    lambda _checked=False, value=command.value: self.commandRequested.emit(value)
                )
            controls.addWidget(button, 1)
            self.command_buttons[command.value] = button
        self.command_buttons[OperatorCommand.ABORT.value].setObjectName("abortCommand")
        self.layout_button = QPushButton("Layout")
        self.layout_button.setMinimumSize(100, 28)
        controls.addWidget(self.layout_button)
        root.addLayout(controls)

        self.workspace = MonitoringWorkspace(create_monitoring_registry(), self)
        root.addWidget(self.workspace, 1)
        self.layout_menu, self.layout_picker = create_layout_menu(
            self.layout_button, self.workspace, self.resetLayoutRequested.emit
        )

        footer = QHBoxLayout()
        self.output_label = QLabel("Output: -")
        self.output_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.new_session_button = QPushButton("Back to setup / New session")
        self.new_session_button.clicked.connect(self.newSessionRequested.emit)
        self.export_button = QPushButton("Export session summary...")
        self.export_button.clicked.connect(self.exportRequested.emit)
        footer.addWidget(self.output_label, 1)
        footer.addWidget(self.new_session_button)
        footer.addWidget(self.export_button)
        root.addLayout(footer)

        self.setStyleSheet(
            'QPushButton[protocolCommand="true"] { padding: 3px 8px; }'
            "QPushButton#abortCommand:enabled { background: #8b1e2d; color: white; "
            "border: 1px solid #6f1421; border-radius: 4px; }"
        )
        self.clear()

    def clear(self) -> None:
        self.workspace.clear_views()
        self.session_label.setText("Session: -")
        self.runtime_label.setText("Runtime: -")
        self.recording_label.setText("Recording: -")
        self.protocol_label.setText("Protocol: -")
        self.phase_label.setText("Phase: -")
        self.progress_label.setText("Progress: -")
        self.output_label.setText("Output: -")
        self.clear_alert()
        self.set_controls(None)

    def show_connecting(self) -> None:
        self.runtime_label.setText("Runtime: connecting")
        self.phase_label.setText("Phase: creating session")
        self.set_controls(None)
        self.primary_action = ""
        self.command_buttons[OperatorCommand.START_PROTOCOL.value].setText(
            "Init subject UI"
        )

    def render(self, state: OperatorStatePayload) -> None:
        view = state.view_state
        acquisition = state.acquisition
        self.session_label.setText(
            f"Session: {state.session_id} - Participant: {state.participant_id}"
        )
        self.runtime_label.setText(f"Runtime: {state.runtime_state}")
        self.recording_label.setText(
            f"Recording: {'active' if acquisition.running else 'stopped'} - "
            f"{acquisition.sample_count} samples"
        )
        self.protocol_label.setText(f"Protocol: {state.engine_state}")
        if state.runtime_state in {"created", "connecting"}:
            self.phase_label.setText("Phase: subject UI not initialized")
        elif state.runtime_state == "ready":
            self.phase_label.setText("Phase: recording ready; protocol not started")
        elif state.runtime_state == "pre_roll":
            self.phase_label.setText("Phase: pre-roll")
        else:
            self.phase_label.setText(
                f"Phase: {view.screen} - {view.remaining_seconds:.1f}s remaining"
            )
        if view.screen == "practice_complete":
            self.progress_label.setText(
                "Progress: Practice complete - awaiting operator"
            )
        elif view.trial_number is None:
            self.progress_label.setText("Progress: no active trial")
        elif view.stage_type == "practice":
            self.progress_label.setText(
                f"Progress: Practice - trial {view.trial_number}/{view.trial_count}"
            )
        else:
            self.progress_label.setText(
                f"Progress: Experiment block {view.block_number}/{view.block_count}, "
                f"trial {view.trial_number}/{view.trial_count}"
            )
        self.output_label.setText(f"Output: {state.session_path}")
        if state.error:
            self.show_alert(state.error, error=True)
        elif state.timing_warnings:
            self.show_alert("\n".join(state.timing_warnings), error=False)
        else:
            self.clear_alert()
        self.workspace.render(state)

    def render_finalized(self, value: SessionFinalizedPayload) -> None:
        self.runtime_label.setText(f"Runtime: {value.state}")
        self.protocol_label.setText(
            f"Protocol: {(value.validation.status if value.validation else value.state)}"
        )
        self.phase_label.setText("Phase: session finalized")
        self.output_label.setText(f"Output: {value.session_path}")
        if value.error:
            self.show_alert(value.error, error=True)
        elif value.timing_warnings:
            self.show_alert("\n".join(value.timing_warnings), error=False)

    def set_controls(self, state) -> None:  # type: ignore[no-untyped-def]
        primary = self.command_buttons[OperatorCommand.START_PROTOCOL.value]
        if state and state.init_subject_ui:
            self.primary_action = "init_subject_ui"
            primary.setText("Init subject UI")
        elif state and state.subject_ui_initializing:
            self.primary_action = ""
            primary.setText("Initializing subject UI...")
        elif state and state.start_experiment:
            self.primary_action = OperatorCommand.START_EXPERIMENT.value
            primary.setText("Start experiment")
        else:
            self.primary_action = OperatorCommand.START_PROTOCOL.value
            primary.setText("Start protocol")
        values = {
            "start_protocol": bool(
                state
                and (
                    state.init_subject_ui
                    or state.start_protocol
                    or state.start_experiment
                )
            ),
            "pause": bool(state and state.pause),
            "resume": bool(state and state.resume),
            "repeat_trial": bool(state and state.repeat_trial),
            "repeat_block": bool(state and state.repeat_block),
            "repeat_last_practice_trial": bool(
                state and state.repeat_last_practice_trial
            ),
            "refit": bool(state and state.refit),
            "abort": bool(state and state.abort),
        }
        requirements = {
            "start_protocol": (
                "Launch the PsychoPy subject UI."
                if state and state.init_subject_ui
                else "Available when the subject UI and recording are ready."
            ),
            "pause": "Available while the protocol is running.",
            "resume": "Available while the protocol is paused.",
            "repeat_trial": "Available during an active trial.",
            "repeat_block": "Available during an active trial.",
            "repeat_last_practice_trial": (
                "Available after the practice stage is complete."
            ),
            "refit": "Available while the protocol is running or paused.",
            "abort": "Available while the session is active.",
        }
        for command, button in self.command_buttons.items():
            button.setEnabled(values[command])
            button.setToolTip("Available" if values[command] else requirements[command])
        terminal = bool(state and state.create_session)
        self.new_session_button.setEnabled(terminal)
        self.export_button.setEnabled(terminal)
        self.layout_button.setEnabled(state is not None)

    def _primary_clicked(self) -> None:
        if self.primary_action == "init_subject_ui":
            self.initSubjectRequested.emit()
        elif self.primary_action == OperatorCommand.START_PROTOCOL.value:
            self.commandRequested.emit(OperatorCommand.START_PROTOCOL.value)
        elif self.primary_action == OperatorCommand.START_EXPERIMENT.value:
            self.commandRequested.emit(OperatorCommand.START_EXPERIMENT.value)

    def show_alert(self, text: str, *, error: bool) -> None:
        self.alert_label.setText(text)
        self.alert_label.setStyleSheet(
            "padding: 6px; color: #b00020; background: #fde7e9;"
            if error
            else "padding: 6px; color: #7a5200; background: #fff4ce;"
        )
        self.alert_label.show()

    def clear_alert(self) -> None:
        self.alert_label.clear()
        self.alert_label.hide()
