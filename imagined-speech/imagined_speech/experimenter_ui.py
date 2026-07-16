"""Experimenter setup, monitoring, and recovery interface."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from imagined_speech.config import (
    ExperimentConfig,
    ResolvedExperiment,
    load_device_profile,
    load_experiment,
)
from imagined_speech.engine import RunState
from imagined_speech.experimenter_settings import (
    SETUP_SCHEMA_VERSION,
    WORKSPACE_SCHEMA_VERSION,
    ExperimenterSettingsStore,
)
from imagined_speech.monitoring_views import (
    AcquisitionHealthView,
    ChannelReceptionView,
    EEGTraceView,
    OperatorAuditView,
    RecentMarkersView,
)
from imagined_speech.monitoring_workspace import (
    DEFAULT_LAYOUT_ID,
    LayoutPicker,
    MonitoringPanelRegistry,
    MonitoringViewDescriptor,
    MonitoringWorkspace,
    create_layout_menu,
)
from imagined_speech.operator import OperatorCommand, OperatorCommandStatus
from imagined_speech.preview import render_preview
from imagined_speech.runtime import SessionRuntime, SessionRuntimeState
from imagined_speech.subject_ui import SubjectWindow
from imagined_speech.window_placement import SubjectWindowPlacementController


class ExperimenterWorkflowState(StrEnum):
    SETUP = "setup"
    CONNECTING = "connecting"
    READY = "ready"
    PROTOCOL_ACTIVE = "protocol_active"
    FINALIZING = "finalizing"
    REVIEW = "review"


@dataclass(frozen=True)
class SessionReviewSummary:
    session_id: str
    status: str
    participant_id: str
    session_label: str | None
    experiment_id: str
    device_profile: str
    session_path: Path
    event_count: int
    operator_command_count: int
    completed_trial_count: int
    completed_phase_count: int
    sample_count: int
    warnings: tuple[str, ...]

    @classmethod
    def from_runtime(cls, runtime: SessionRuntime) -> SessionReviewSummary:
        report = runtime.validation_report
        if report is None:
            raise RuntimeError("cannot enter review before session validation")
        return cls(
            session_id=report.session_id,
            status=report.status,
            participant_id=runtime.writer.participant_id,
            session_label=runtime.writer.session_label,
            experiment_id=runtime.resolved.config.experiment_id,
            device_profile=runtime.resolved.device.profile_id,
            session_path=report.session_path,
            event_count=report.event_count,
            operator_command_count=report.operator_command_count,
            completed_trial_count=report.trial_count,
            completed_phase_count=report.phase_count,
            sample_count=report.sample_count,
            warnings=tuple(report.warnings),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "status": self.status,
            "participant_id": self.participant_id,
            "session_label": self.session_label,
            "experiment_id": self.experiment_id,
            "device_profile": self.device_profile,
            "session_path": str(self.session_path),
            "event_count": self.event_count,
            "operator_command_count": self.operator_command_count,
            "completed_trial_count": self.completed_trial_count,
            "completed_phase_count": self.completed_phase_count,
            "sample_count": self.sample_count,
            "warnings": list(self.warnings),
            "qc_status": "not implemented until roadmap Milestone 5",
        }


class ExperimenterWindow(QMainWindow):
    """Long-lived desktop shell; each recording receives a fresh runtime."""

    def __init__(
        self,
        initial_config: Path,
        *,
        participant: str = "P001",
        output_root: Path | None = None,
        session_label: str | None = None,
        settings_store: ExperimenterSettingsStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Imagined Speech - Experimenter")
        self.resize(1440, 900)
        self.settings = settings_store or ExperimenterSettingsStore()
        self.subject_placement = SubjectWindowPlacementController(self.settings)
        self.workflow_state = ExperimenterWorkflowState.SETUP
        self.runtime: SessionRuntime | None = None
        self.review_summary: SessionReviewSummary | None = None
        self.subject_window: SubjectWindow | None = None
        self._base_resolved: ResolvedExperiment | None = None
        self._startup_thread: threading.Thread | None = None
        self._startup_result: tuple[int, str | None] | None = None
        self._startup_error_shown = False
        self._session_generation = 0
        self._settings_warning: str | None = None
        self._workspace_warning: str | None = None
        self._audio_context_for_confirmation: str | None = None
        self._setup_saved_generation = -1
        self._close_after_finalize = False
        self._closing_committed = False
        self._initial_config = initial_config.expanduser().resolve()
        self._initial_participant = participant
        self._initial_output_root = output_root
        self._initial_session_label = session_label or "RUN001"

        self.panel_registry = self._create_panel_registry()
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.setup_page = self._build_setup_page()
        self.live_page = self._build_live_page()
        self.pages.addWidget(self.setup_page)
        self.pages.addWidget(self.live_page)

        self.workspace_save_timer = QTimer(self)
        self.workspace_save_timer.setSingleShot(True)
        self.workspace_save_timer.setInterval(350)
        self.workspace_save_timer.timeout.connect(self._save_workspace_settings)
        self.workspace.stateChanged.connect(
            lambda: self.workspace_save_timer.start()
        )

        self._restore_window_geometry()
        self._restore_workspace_settings()
        self._restore_setup_settings()
        self._set_workflow_state(ExperimenterWorkflowState.SETUP)

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    @staticmethod
    def _create_panel_registry() -> MonitoringPanelRegistry:
        registry = MonitoringPanelRegistry()
        for descriptor in (
            MonitoringViewDescriptor("live_eeg", "Live EEG", EEGTraceView),
            MonitoringViewDescriptor(
                "channel_reception", "Channel reception", ChannelReceptionView
            ),
            MonitoringViewDescriptor(
                "recent_markers", "Recent protocol markers", RecentMarkersView
            ),
            MonitoringViewDescriptor(
                "operator_audit", "Operator command audit", OperatorAuditView
            ),
            MonitoringViewDescriptor(
                "acquisition_health",
                "Acquisition and storage health",
                AcquisitionHealthView,
            ),
        ):
            registry.register(descriptor)
        return registry

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        title = QLabel("Session setup")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        root.addWidget(title)

        form_group = QGroupBox("Identity and protocol")
        form = QFormLayout(form_group)
        self.participant_edit = QLineEdit(self._initial_participant)
        self.session_label_edit = QLineEdit(self._initial_session_label)
        self.config_edit = QLineEdit(str(self._initial_config))
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_edit)
        config_browse = QPushButton("Browse...")
        config_browse.clicked.connect(self._browse_config)
        config_row.addWidget(config_browse)
        config_load = QPushButton("Load")
        config_load.clicked.connect(self._load_configuration)
        config_row.addWidget(config_load)
        self.device_edit = QLineEdit()
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_edit)
        device_browse = QPushButton("Browse...")
        device_browse.clicked.connect(self._browse_device)
        device_row.addWidget(device_browse)
        initial_output = (
            str(self._initial_output_root.expanduser().resolve())
            if self._initial_output_root
            else ""
        )
        self.output_edit = QLineEdit(initial_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_browse = QPushButton("Browse...")
        output_browse.clicked.connect(self._browse_output)
        output_row.addWidget(output_browse)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.subject_screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            size = screen.size()
            label = f"{index}: {screen.name()} ({size.width()}x{size.height()})"
            self.subject_screen_combo.addItem(label, index)
        if self.subject_screen_combo.count() > 1:
            self.subject_screen_combo.setCurrentIndex(1)
        self.audio_ready = QCheckBox("Audio output and volume checked")
        self.montage_label = QLabel("-")
        self.montage_label.setWordWrap(True)
        self.subject_screen_combo.currentIndexChanged.connect(self._refresh_preview)
        self.audio_ready.stateChanged.connect(self._audio_readiness_changed)
        self.seed_spin.valueChanged.connect(self._refresh_preview)
        self.device_edit.editingFinished.connect(self._device_changed)

        form.addRow("Participant ID", self.participant_edit)
        form.addRow("Session label", self.session_label_edit)
        form.addRow("Protocol configuration", config_row)
        form.addRow("Device profile", device_row)
        form.addRow("Random seed", self.seed_spin)
        form.addRow("Output directory", output_row)
        form.addRow("Subject display", self.subject_screen_combo)
        form.addRow("Montage", self.montage_label)
        form.addRow("Audio readiness", self.audio_ready)
        root.addWidget(form_group)

        preview_group = QGroupBox("Validated preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(240)
        self.setup_warnings = QLabel()
        self.setup_warnings.setWordWrap(True)
        preview_layout.addWidget(self.preview_text)
        preview_layout.addWidget(self.setup_warnings)
        root.addWidget(preview_group, 1)

        setup_actions = QHBoxLayout()
        self.reset_setup_button = QPushButton("Reset saved setup")
        self.reset_setup_button.clicked.connect(self._reset_saved_setup)
        setup_actions.addWidget(self.reset_setup_button)
        setup_actions.addStretch(1)
        self.start_button = QPushButton("Connect and start recording")
        self.start_button.setMinimumHeight(44)
        self.start_button.clicked.connect(self._start_session)
        setup_actions.addWidget(self.start_button)
        root.addLayout(setup_actions)
        return page

    def _build_live_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        header = QGridLayout()
        self.session_id_label = QLabel("Session: -")
        self.runtime_state_label = QLabel("Runtime: -")
        self.recording_state_label = QLabel("Recording: -")
        self.protocol_state_label = QLabel("Protocol: -")
        self.phase_label = QLabel("Phase: -")
        self.progress_label = QLabel("Progress: -")
        for widget in (
            self.session_id_label,
            self.runtime_state_label,
            self.recording_state_label,
            self.protocol_state_label,
            self.phase_label,
            self.progress_label,
        ):
            widget.setStyleSheet("font-size: 16px;")
        header.addWidget(self.session_id_label, 0, 0, 1, 2)
        header.addWidget(self.runtime_state_label, 1, 0)
        header.addWidget(self.recording_state_label, 1, 1)
        header.addWidget(self.protocol_state_label, 2, 0)
        header.addWidget(self.phase_label, 2, 1)
        header.addWidget(self.progress_label, 3, 0, 1, 2)
        root.addLayout(header)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.command_buttons: dict[OperatorCommand, QPushButton] = {}
        for command, label in (
            (OperatorCommand.START_PROTOCOL, "Start protocol"),
            (OperatorCommand.PAUSE, "Pause"),
            (OperatorCommand.RESUME, "Resume"),
            (OperatorCommand.REPEAT_TRIAL, "Repeat trial"),
            (OperatorCommand.REPEAT_BLOCK, "Repeat block"),
            (OperatorCommand.REFIT, "Refit / note"),
            (OperatorCommand.ABORT, "Abort"),
        ):
            button = QPushButton(label)
            button.setProperty("protocolCommand", True)
            button.setMinimumHeight(28)
            button.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
            button.clicked.connect(
                lambda checked=False, item=command: self._command(item)
            )
            controls.addWidget(button, 1)
            self.command_buttons[command] = button
        self.start_protocol_button = self.command_buttons[OperatorCommand.START_PROTOCOL]
        self.command_buttons[OperatorCommand.ABORT].setObjectName("abortCommand")
        self.layout_button = QPushButton("Layout")
        self.layout_button.setMinimumSize(100, 28)
        self.layout_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        controls.addWidget(self.layout_button)
        root.addLayout(controls)

        # This is the only region replaced by the configurable workspace.
        self.workspace = MonitoringWorkspace(self.panel_registry, page)
        root.addWidget(self.workspace, 1)
        self.layout_menu, self.layout_picker = create_layout_menu(
            self.layout_button, self.workspace, self._reset_monitoring_layout
        )

        footer = QHBoxLayout()
        self.session_path_label = QLabel("Output: -")
        self.session_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.new_session_button = QPushButton("Back to setup / New session")
        self.new_session_button.clicked.connect(self._new_session)
        self.export_button = QPushButton("Export session summary...")
        self.export_button.clicked.connect(self._export_summary)
        footer.addWidget(self.session_path_label, 1)
        footer.addWidget(self.new_session_button)
        footer.addWidget(self.export_button)
        root.addLayout(footer)
        page.setStyleSheet(
            "QPushButton[protocolCommand=\"true\"] { padding: 3px 8px; }"
            "QPushButton#abortCommand:enabled { background: #8b1e2d; color: white; "
            "border: 1px solid #6f1421; border-radius: 4px; }"
        )
        return page

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Experiment configuration", self.config_edit.text(), "YAML (*.yaml *.yml)"
        )
        if path:
            self.config_edit.setText(path)
            self._load_configuration()

    def _browse_device(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Device profile", self.device_edit.text(), "YAML (*.yaml *.yml)"
        )
        if path:
            self.device_edit.setText(path)
            self._device_changed()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Session output directory", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _load_configuration(self) -> None:
        previous_context = self._audio_context_for_confirmation
        try:
            resolved = load_experiment(Path(self.config_edit.text()).expanduser())
            self._base_resolved = resolved
            self.device_edit.setText(str(resolved.device_path))
            self.seed_spin.setValue(resolved.config.random_seed)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(resolved.output_root))
            desired = resolved.config.presentation.subject_screen
            if desired < self.subject_screen_combo.count():
                self.subject_screen_combo.setCurrentIndex(desired)
            if previous_context and previous_context != self._audio_context():
                self.audio_ready.setChecked(False)
            self._refresh_preview()
        except Exception as exc:
            self._base_resolved = None
            self.preview_text.setPlainText(f"Configuration error:\n{exc}")
            self.setup_warnings.setText("Resolve the configuration error before starting.")
            self.setup_warnings.setStyleSheet("color: #b00020;")
        self._update_control_availability()

    def _resolved_setup(self) -> ResolvedExperiment:
        if self._base_resolved is None:
            raise ValueError("no valid experiment configuration is loaded")
        device_path = Path(self.device_edit.text()).expanduser().resolve()
        device = load_device_profile(device_path)
        config_data = self._base_resolved.config.model_dump(mode="python")
        config_data["random_seed"] = self.seed_spin.value()
        config_data["device_profile"] = device_path
        config = ExperimentConfig.model_validate(config_data)
        return replace(
            self._base_resolved,
            config=config,
            device=device,
            device_path=device_path,
        )

    def _refresh_preview(self, *_args) -> None:  # type: ignore[no-untyped-def]
        try:
            resolved = self._resolved_setup()
            self.preview_text.setPlainText(render_preview(resolved))
            montage = ", ".join(channel.label for channel in resolved.device.eeg_channels)
            self.montage_label.setText(
                f"{montage}; reference: {resolved.device.reference}; ground: {resolved.device.ground}"
            )
            warnings = [item for item in (self._settings_warning, self._workspace_warning) if item]
            if self.subject_screen_combo.count() < 2:
                warnings.append("Only one display detected; subject and experimenter views will share it.")
            if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
                warnings.append("Audio is enabled but readiness has not been confirmed.")
            if resolved.device.backend == "lsl":
                warnings.append("The configured LSL stream must be publishing before connection.")
            elif resolved.device.backend == "cyton":
                warnings.append("Cyton hardware and serial-port configuration are required.")
            self.setup_warnings.setText(
                "Warnings:\n- " + "\n- ".join(warnings) if warnings else "No setup warnings."
            )
            self.setup_warnings.setStyleSheet(
                "color: #9a6700;" if warnings else "color: #2e7d32;"
            )
        except Exception as exc:
            self.preview_text.setPlainText(f"Setup error:\n{exc}")
            self.setup_warnings.setText("Resolve the setup error before starting.")
            self.setup_warnings.setStyleSheet("color: #b00020;")
        self._update_control_availability()

    def _audio_context(self) -> str:
        return json.dumps(
            [
                str(Path(self.config_edit.text()).expanduser().resolve()),
                str(Path(self.device_edit.text()).expanduser().resolve()),
            ],
            separators=(",", ":"),
        )

    def _audio_readiness_changed(self, state: int) -> None:
        self._audio_context_for_confirmation = (
            self._audio_context() if state == Qt.CheckState.Checked.value else None
        )
        self._refresh_preview()

    def _device_changed(self) -> None:
        if (
            self._audio_context_for_confirmation
            and self._audio_context_for_confirmation != self._audio_context()
        ):
            self.audio_ready.setChecked(False)
        self._refresh_preview()

    def _start_session(self) -> None:
        if self.workflow_state != ExperimenterWorkflowState.SETUP:
            return
        try:
            resolved = self._resolved_setup()
            participant = self.participant_edit.text().strip()
            if not participant:
                raise ValueError("participant ID is required")
            if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
                raise ValueError("confirm audio readiness before recording")
            output = Path(self.output_edit.text()).expanduser() if self.output_edit.text().strip() else None
            runtime = SessionRuntime(
                resolved,
                participant,
                output_root=output,
                session_label=self.session_label_edit.text().strip() or None,
            )
            self._session_generation += 1
            generation = self._session_generation
            self.runtime = runtime
            self.review_summary = None
            self._clear_session_displays()
            self._startup_result = None
            self._startup_error_shown = False
            self._close_after_finalize = False
            self.pages.setCurrentWidget(self.live_page)
            self.session_id_label.setText(
                f"Session: {runtime.writer.session_id} - Participant: {runtime.writer.participant_id}"
            )
            self.session_path_label.setText(f"Output: {runtime.session_path}")
            self._set_workflow_state(ExperimenterWorkflowState.CONNECTING)
            self._startup_thread = threading.Thread(
                target=self._start_runtime,
                args=(runtime, generation),
                name="session-connection",
                daemon=True,
            )
            self._startup_thread.start()
        except Exception as exc:
            self._close_subject_window()
            QMessageBox.critical(self, "Cannot start session", str(exc))

    def _start_runtime(self, runtime: SessionRuntime, generation: int) -> None:
        error: str | None = None
        try:
            runtime.start()
        except Exception as exc:
            error = str(exc)
        if generation == self._session_generation:
            self._startup_result = (generation, error)

    def _open_subject_window(self, runtime: SessionRuntime) -> None:
        if self.subject_window is not None:
            return
        resolved = runtime.resolved
        subject_window = SubjectWindow(
            runtime.engine, resolved, auto_start=False, drive_engine=False
        )
        self.subject_window = subject_window
        screens = QApplication.screens()
        subject_index = min(int(self.subject_screen_combo.currentData() or 0), len(screens) - 1)
        subject_screen = screens[subject_index]
        subject_window.aboutToClose.connect(
            lambda: self.subject_placement.capture_window(
                subject_window, subject_screen, subject_index
            )
        )
        self.subject_placement.open_window(
            subject_window,
            subject_screen,
            subject_index,
            resolved.config.presentation.window_mode,
        )

    def _tick(self) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        if self._startup_thread is not None and not self._startup_thread.is_alive():
            self._startup_thread.join(timeout=0)
            self._startup_thread = None
        if (
            self._startup_result is not None
            and self._startup_result[0] == self._session_generation
            and self._startup_result[1]
            and not self._startup_error_shown
        ):
            self._startup_error_shown = True
            QMessageBox.critical(
                self,
                "Session connection failed",
                f"{self._startup_result[1]}\n\nPartial package: {runtime.session_path}",
            )
        try:
            runtime.tick()
        except Exception as exc:
            self.runtime_state_label.setText(f"Runtime error: {exc}")
        self._render_live(runtime)
        self._synchronize_workflow(runtime)
        if (
            self._close_after_finalize
            and runtime.state in {SessionRuntimeState.FINALIZED, SessionRuntimeState.FAILED}
        ):
            self._closing_committed = True
            self.close()

    def _synchronize_workflow(self, runtime: SessionRuntime) -> None:
        target = {
            SessionRuntimeState.CREATED: ExperimenterWorkflowState.CONNECTING,
            SessionRuntimeState.CONNECTING: ExperimenterWorkflowState.CONNECTING,
            SessionRuntimeState.READY: ExperimenterWorkflowState.READY,
            SessionRuntimeState.PRE_ROLL: ExperimenterWorkflowState.PROTOCOL_ACTIVE,
            SessionRuntimeState.RUNNING: ExperimenterWorkflowState.PROTOCOL_ACTIVE,
            SessionRuntimeState.POST_ROLL: ExperimenterWorkflowState.FINALIZING,
            SessionRuntimeState.FINALIZED: ExperimenterWorkflowState.REVIEW,
            SessionRuntimeState.FAILED: ExperimenterWorkflowState.REVIEW,
        }[runtime.state]
        if target == ExperimenterWorkflowState.READY:
            self._open_subject_window(runtime)
            if self._setup_saved_generation != self._session_generation:
                self._save_setup_settings()
                self._setup_saved_generation = self._session_generation
        if target == ExperimenterWorkflowState.REVIEW:
            self._enter_review(runtime)
        else:
            self._set_workflow_state(target)

    def _enter_review(self, runtime: SessionRuntime) -> None:
        try:
            summary = SessionReviewSummary.from_runtime(runtime)
        except RuntimeError as exc:
            self.runtime_state_label.setText(f"Review unavailable: {exc}")
            return
        self.review_summary = summary
        self._close_subject_window()
        self.runtime = None
        self._startup_result = None
        self._set_workflow_state(ExperimenterWorkflowState.REVIEW)

    def _set_workflow_state(self, state: ExperimenterWorkflowState) -> None:
        self.workflow_state = state
        self.pages.setCurrentWidget(
            self.setup_page if state == ExperimenterWorkflowState.SETUP else self.live_page
        )
        self._update_control_availability()

    def _update_control_availability(self) -> None:
        runtime = self.runtime
        engine_state = runtime.engine.state if runtime else None
        in_trial = bool(
            runtime
            and runtime.engine.current_action is not None
            and runtime.engine.current_action.context.trial_id is not None
        )
        running = engine_state == RunState.RUNNING
        paused = engine_state == RunState.PAUSED
        ready = self.workflow_state == ExperimenterWorkflowState.READY
        active_pre_roll = (
            self.workflow_state == ExperimenterWorkflowState.PROTOCOL_ACTIVE
            and engine_state == RunState.READY
        )
        availability = {
            OperatorCommand.START_PROTOCOL: ready,
            OperatorCommand.PAUSE: running,
            OperatorCommand.RESUME: paused,
            OperatorCommand.REPEAT_TRIAL: (running or paused) and in_trial,
            OperatorCommand.REPEAT_BLOCK: (running or paused) and in_trial,
            OperatorCommand.REFIT: running or paused,
            OperatorCommand.ABORT: ready or active_pre_roll or running or paused,
        }
        requirements = {
            OperatorCommand.START_PROTOCOL: "Available after recording starts.",
            OperatorCommand.PAUSE: "Available while the protocol is running.",
            OperatorCommand.RESUME: "Available while the protocol is paused.",
            OperatorCommand.REPEAT_TRIAL: "Available during an active trial.",
            OperatorCommand.REPEAT_BLOCK: "Available during an active trial.",
            OperatorCommand.REFIT: "Available while the protocol is active.",
            OperatorCommand.ABORT: "Available while recording is active.",
        }
        for command, button in self.command_buttons.items():
            enabled = bool(availability[command])
            button.setEnabled(enabled)
            button.setToolTip("Available" if enabled else requirements[command])
        self.start_button.setEnabled(
            self.workflow_state == ExperimenterWorkflowState.SETUP
            and self._base_resolved is not None
        )
        self.new_session_button.setEnabled(
            self.workflow_state == ExperimenterWorkflowState.REVIEW
        )
        self.export_button.setEnabled(
            self.workflow_state == ExperimenterWorkflowState.REVIEW
            and self.review_summary is not None
        )
        self.layout_button.setEnabled(
            self.workflow_state != ExperimenterWorkflowState.SETUP
        )

    def _render_live(self, runtime: SessionRuntime) -> None:
        view = runtime.engine.view_state()
        snapshot = runtime.acquisition_snapshot()
        self.runtime_state_label.setText(f"Runtime: {runtime.state.value}")
        self.recording_state_label.setText(
            f"Recording: {'active' if snapshot.running else 'stopped'} - {snapshot.sample_count} samples"
        )
        self.protocol_state_label.setText(f"Protocol: {view.run_state.value}")
        if runtime.state == SessionRuntimeState.READY:
            self.phase_label.setText("Phase: recording ready; protocol not started")
        elif runtime.state == SessionRuntimeState.PRE_ROLL:
            self.phase_label.setText("Phase: pre-roll")
        else:
            self.phase_label.setText(
                f"Phase: {view.screen} - {view.remaining_seconds:.1f}s remaining"
            )
        if view.trial_number is None:
            self.progress_label.setText("Progress: no active trial")
        else:
            self.progress_label.setText(
                f"Progress: {view.block_type} block {view.block_number}/{view.block_count}, "
                f"trial {view.trial_number}/{view.trial_count}"
            )
        indexes = tuple(
            int(value)
            for value in runtime.acquisition.backend.metadata.get(
                "eeg_channel_indexes", range(len(runtime.resolved.device.eeg_channels))
            )
        )
        labels = tuple(channel.label for channel in runtime.resolved.device.eeg_channels)
        for widget in self.workspace.widgets_for("live_eeg"):
            assert isinstance(widget, EEGTraceView)
            widget.render(snapshot.recent_samples, indexes, labels)
        for widget in self.workspace.widgets_for("channel_reception"):
            assert isinstance(widget, ChannelReceptionView)
            widget.render(snapshot.recent_samples, indexes, labels)
        for widget in self.workspace.widgets_for("recent_markers"):
            assert isinstance(widget, RecentMarkersView)
            widget.render(runtime.event_memory.events)
        for widget in self.workspace.widgets_for("operator_audit"):
            assert isinstance(widget, OperatorAuditView)
            widget.render(runtime.operator_records)
        for widget in self.workspace.widgets_for("acquisition_health"):
            assert isinstance(widget, AcquisitionHealthView)
            widget.render(snapshot, runtime.session_path)
        self._update_control_availability()

    def _command(self, command: OperatorCommand) -> None:
        runtime = self.runtime
        button = self.command_buttons[command]
        if runtime is None or not button.isEnabled():
            return
        note: str | None = None
        if command == OperatorCommand.REFIT:
            note, accepted = QInputDialog.getText(
                self, "Refit note", "Describe the electrode/headset adjustment:"
            )
            if not accepted:
                return
        if command == OperatorCommand.ABORT:
            subject = "recording" if not runtime.protocol_started else "protocol"
            answer = QMessageBox.question(
                self,
                "Abort session",
                f"Abort the {subject} and finalize the partial package?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            record = runtime.execute(command, note=note)
            if record.status == OperatorCommandStatus.REJECTED:
                QMessageBox.warning(self, "Command rejected", record.reason)
        except Exception as exc:
            QMessageBox.critical(self, "Command error", str(exc))
        self._synchronize_workflow(runtime)

    def _new_session(self) -> None:
        if self.workflow_state != ExperimenterWorkflowState.REVIEW:
            return
        self._close_subject_window()
        self.review_summary = None
        self._session_generation += 1
        self._clear_session_displays()
        self._set_workflow_state(ExperimenterWorkflowState.SETUP)
        self._refresh_preview()

    def _clear_session_displays(self) -> None:
        self.workspace.clear_views()
        self.session_id_label.setText("Session: -")
        self.runtime_state_label.setText("Runtime: -")
        self.recording_state_label.setText("Recording: -")
        self.protocol_state_label.setText("Protocol: -")
        self.phase_label.setText("Phase: -")
        self.progress_label.setText("Progress: -")
        self.session_path_label.setText("Output: -")

    def _close_subject_window(self) -> None:
        if self.subject_window is None:
            return
        self.subject_window.timer.stop()
        self.subject_window.close()
        self.subject_window.deleteLater()
        self.subject_window = None

    def _export_summary(self) -> None:
        summary = self.review_summary
        if summary is None:
            return
        default = summary.session_path.parent / f"{summary.session_path.name}-summary.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export session summary", str(default), "JSON (*.json)"
        )
        if path:
            Path(path).write_text(
                json.dumps(summary.as_json(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    @staticmethod
    def _as_bool(value: object) -> bool:
        return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _as_int(value: object, default: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _json_mapping(value: object) -> dict[str, object] | None:
        try:
            parsed = json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _restore_setup_settings(self) -> None:
        version = self.settings.int_value("schema/version")
        if version is not None and version != SETUP_SCHEMA_VERSION:
            self._settings_warning = f"Saved setup schema {version} is unsupported; defaults were used."
            self._load_configuration()
            return
        config_value = self.settings.value("setup/config_path")
        if config_value:
            saved_config = Path(str(config_value)).expanduser()
            if saved_config.is_file():
                self.config_edit.setText(str(saved_config.resolve()))
            else:
                self._settings_warning = f"Saved protocol configuration is unavailable: {saved_config}."
        self._load_configuration()
        if self._base_resolved is None and self.config_edit.text() != str(self._initial_config):
            self.config_edit.setText(str(self._initial_config))
            self._load_configuration()
        for key, edit in (
            ("setup/participant_id", self.participant_edit),
            ("setup/session_label", self.session_label_edit),
            ("setup/output_root", self.output_edit),
        ):
            value = self.settings.value(key)
            if value:
                edit.setText(str(value))
        device_value = self.settings.value("setup/device_path")
        if device_value:
            saved_device = Path(str(device_value)).expanduser()
            if saved_device.is_file():
                self.device_edit.setText(str(saved_device.resolve()))
            else:
                warning = f"Saved device profile is unavailable: {saved_device}."
                self._settings_warning = " ".join(filter(None, (self._settings_warning, warning)))
        self.seed_spin.setValue(
            self._as_int(self.settings.value("setup/random_seed"), self.seed_spin.value())
        )
        self._restore_screen(
            self.subject_screen_combo, self.settings.value("setup/subject_screen")
        )
        saved_context = str(self.settings.value("setup/audio_context", ""))
        ready = self._as_bool(self.settings.value("setup/audio_ready", False))
        if ready and saved_context == self._audio_context():
            self.audio_ready.setChecked(True)
            self._audio_context_for_confirmation = saved_context
        else:
            self.audio_ready.setChecked(False)
            self._audio_context_for_confirmation = None
        self._refresh_preview()

    def _save_setup_settings(self) -> bool:
        try:
            self._resolved_setup()
            participant = self.participant_edit.text().strip()
            if not participant:
                return False
        except Exception:
            return False
        values = {
            "schema/version": SETUP_SCHEMA_VERSION,
            "setup/participant_id": participant,
            "setup/session_label": self.session_label_edit.text().strip(),
            "setup/config_path": str(Path(self.config_edit.text()).expanduser().resolve()),
            "setup/device_path": str(Path(self.device_edit.text()).expanduser().resolve()),
            "setup/output_root": self.output_edit.text().strip(),
            "setup/random_seed": self.seed_spin.value(),
            "setup/subject_screen": self._screen_identity(self.subject_screen_combo),
            "setup/audio_ready": self.audio_ready.isChecked(),
            "setup/audio_context": self._audio_context_for_confirmation or "",
        }
        for key, value in values.items():
            self.settings.set_value(key, value)
        self.settings.sync()
        return True

    def _reset_saved_setup(self) -> None:
        self.settings.clear_setup()
        self._settings_warning = None
        self.participant_edit.setText(self._initial_participant)
        self.session_label_edit.setText(self._initial_session_label)
        self.config_edit.setText(str(self._initial_config))
        self.output_edit.setText(
            str(self._initial_output_root.expanduser().resolve()) if self._initial_output_root else ""
        )
        self.audio_ready.setChecked(False)
        self._load_configuration()

    def _screen_identity(self, combo: QComboBox) -> str:
        index = int(combo.currentData() or 0)
        screens = QApplication.screens()
        name = screens[index].name() if 0 <= index < len(screens) else ""
        return json.dumps({"name": name, "index": index}, separators=(",", ":"))

    def _restore_screen(self, combo: QComboBox, value: object) -> None:
        identity = self._json_mapping(value)
        if identity is None:
            return
        screens = QApplication.screens()
        name = identity.get("name")
        for index, screen in enumerate(screens):
            if name and screen.name() == name:
                combo.setCurrentIndex(index)
                return
        fallback = self._as_int(identity.get("index"), 0)
        combo.setCurrentIndex(min(max(fallback, 0), max(0, combo.count() - 1)))

    def _restore_workspace_settings(self) -> None:
        version = self.settings.int_value("workspace/schema_version")
        if version is None:
            self.workspace.reset()
            self.layout_picker.set_current(DEFAULT_LAYOUT_ID)
            return
        if version != WORKSPACE_SCHEMA_VERSION:
            self._workspace_warning = f"Saved workspace schema {version} is unsupported; layout defaults were used."
            self.workspace.reset()
            self.layout_picker.set_current(DEFAULT_LAYOUT_ID)
            return
        assignments = self._json_mapping(self.settings.value("workspace/pane_assignments"))
        sizes = self._json_mapping(self.settings.value("workspace/splitter_sizes"))
        valid = self.workspace.restore_preferences(
            self.settings.value("workspace/layout_id", DEFAULT_LAYOUT_ID),
            assignments,
            sizes,
        )
        self.layout_picker.set_current(self.workspace.current_layout_id)
        if not valid:
            self._workspace_warning = "Some saved workspace values were invalid and fell back to defaults."

    def _save_workspace_settings(self) -> None:
        layout_id, assignments, splitter_sizes = self.workspace.preferences()
        self.settings.set_value("workspace/schema_version", WORKSPACE_SCHEMA_VERSION)
        self.settings.set_value("workspace/layout_id", layout_id)
        self.settings.set_value("workspace/pane_assignments", json.dumps(assignments, separators=(",", ":")))
        self.settings.set_value("workspace/splitter_sizes", json.dumps(splitter_sizes, separators=(",", ":")))
        self.settings.sync()

    def _reset_monitoring_layout(self) -> None:
        self.workspace.reset()
        self.layout_picker.set_current(DEFAULT_LAYOUT_ID)
        self._workspace_warning = None
        self._save_workspace_settings()

    def _restore_window_geometry(self) -> None:
        geometry = self.settings.byte_array("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        screens = QApplication.screens()
        if screens and not any(
            self.frameGeometry().intersects(screen.availableGeometry()) for screen in screens
        ):
            self.move(screens[0].availableGeometry().topLeft())

    def _save_window_geometry(self) -> None:
        self.settings.set_value("window/geometry", self.saveGeometry())
        self.settings.sync()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._startup_thread is not None and self._startup_thread.is_alive():
            QMessageBox.information(
                self,
                "Connection in progress",
                "Wait for the bounded device connection attempt to finish before closing.",
            )
            event.ignore()
            return
        runtime = self.runtime
        if (
            not self._closing_committed
            and runtime is not None
            and runtime.state not in {SessionRuntimeState.FINALIZED, SessionRuntimeState.FAILED}
        ):
            answer = QMessageBox.question(
                self,
                "Close experimenter",
                "Close the application and finalize the current recording?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if runtime.engine.state in {RunState.READY, RunState.RUNNING, RunState.PAUSED}:
                runtime.execute(OperatorCommand.ABORT, note="Experimenter application closed")
                self._close_after_finalize = True
                event.ignore()
                return
            if runtime.state == SessionRuntimeState.POST_ROLL:
                self._close_after_finalize = True
                event.ignore()
                return
            runtime.close()
        self._save_setup_settings()
        self._save_workspace_settings()
        self._save_window_geometry()
        self._close_subject_window()
        self.timer.stop()
        event.accept()


def run_experimenter_window(
    config_path: Path,
    *,
    participant: str = "P001",
    output_root: Path | None = None,
    session_label: str | None = None,
) -> int:
    app = QApplication.instance() or QApplication([])
    window = ExperimenterWindow(
        config_path,
        participant=participant,
        output_root=output_root,
        session_label=session_label,
    )
    window.show()
    return app.exec()
