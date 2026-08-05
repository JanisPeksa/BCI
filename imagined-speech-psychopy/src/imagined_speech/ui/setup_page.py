"""Session setup scene for the experimenter application."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from imagined_speech.config import (
    ResolvedExperiment,
    SubjectWindowMode,
    load_experiment,
    resolve_session_setup,
)
from imagined_speech.ipc.messages import (
    CreateSessionPayload,
    SubjectDisplayTargetPayload,
)
from imagined_speech.planning.preview import render_preview
from imagined_speech.ui.display_selection import (
    build_subject_display_targets,
    display_target_label,
)


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class SessionSetupPage(QWidget):
    createRequested = pyqtSignal()
    resetRequested = pyqtSignal()

    def __init__(
        self,
        initial_config: Path,
        participant: str,
        session_label: str | None,
        output_root: Path | None,
    ) -> None:
        super().__init__()
        self.initial_config = initial_config.expanduser().resolve()
        self.initial_participant = participant
        self.initial_session_label = session_label or "RUN001"
        self.initial_output_root = output_root
        self._base_resolved: ResolvedExperiment | None = None
        self._subject_displays: tuple[SubjectDisplayTargetPayload, ...] = ()
        self._audio_context_for_confirmation: str | None = None
        self._service_can_create = False
        self._build_ui()
        self.load_configuration()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel("Session setup")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        root.addWidget(title)

        form_group = QGroupBox("Identity, protocol, and presentation")
        form = QFormLayout(form_group)
        self.participant_edit = QLineEdit(self.initial_participant)
        self.session_label_edit = QLineEdit(self.initial_session_label)
        self.config_edit = QLineEdit(str(self.initial_config))
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_edit)
        config_browse = QPushButton("Browse...")
        config_browse.clicked.connect(self._browse_config)
        config_row.addWidget(config_browse)
        config_load = QPushButton("Load")
        config_load.clicked.connect(self.load_configuration)
        config_row.addWidget(config_load)

        self.device_edit = QLineEdit()
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_edit)
        device_browse = QPushButton("Browse...")
        device_browse.clicked.connect(self._browse_device)
        device_row.addWidget(device_browse)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.output_edit = QLineEdit(
            str(self.initial_output_root.expanduser().resolve())
            if self.initial_output_root
            else ""
        )
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_browse = QPushButton("Browse...")
        output_browse.clicked.connect(self._browse_output)
        output_row.addWidget(output_browse)

        self.subject_screen_combo = QComboBox()
        self.subject_screen_combo.addItem("Use session configuration", None)
        self._subject_displays = build_subject_display_targets(QApplication.screens())
        for target in self._subject_displays:
            self.subject_screen_combo.addItem(
                display_target_label(target), target.model_dump(mode="json")
            )
        self.window_mode_combo = QComboBox()
        self.window_mode_combo.addItem("Use session configuration", None)
        for mode in SubjectWindowMode:
            self.window_mode_combo.addItem(mode.value, mode.value)

        self.audio_ready = QCheckBox("Audio output and volume checked")
        self.montage_label = QLabel("-")
        self.montage_label.setWordWrap(True)

        form.addRow("Participant ID", self.participant_edit)
        form.addRow("Session label", self.session_label_edit)
        form.addRow("Protocol configuration", config_row)
        form.addRow("Device profile", device_row)
        form.addRow("Random seed", self.seed_spin)
        form.addRow("Output directory", output_row)
        form.addRow("Subject display override", self.subject_screen_combo)
        form.addRow("Subject window override", self.window_mode_combo)
        form.addRow("Montage", self.montage_label)
        form.addRow("Audio readiness", self.audio_ready)
        root.addWidget(form_group)

        preview_group = QGroupBox("Validated preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(220)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.service_label = QLabel("Connecting to backend...")
        self.service_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_text)
        preview_layout.addWidget(self.warning_label)
        preview_layout.addWidget(self.service_label)
        root.addWidget(preview_group, 1)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset saved setup")
        self.reset_button.clicked.connect(self.resetRequested.emit)
        actions.addWidget(self.reset_button)
        actions.addStretch(1)
        self.create_button = QPushButton("Create session")
        self.create_button.setMinimumHeight(44)
        self.create_button.clicked.connect(self.createRequested.emit)
        actions.addWidget(self.create_button)
        root.addLayout(actions)

        self.config_edit.editingFinished.connect(self.load_configuration)
        self.device_edit.editingFinished.connect(self._setup_changed)
        self.participant_edit.textChanged.connect(self._refresh_availability)
        self.session_label_edit.textChanged.connect(self._refresh_availability)
        self.seed_spin.valueChanged.connect(self._setup_changed)
        self.subject_screen_combo.currentIndexChanged.connect(self._setup_changed)
        self.window_mode_combo.currentIndexChanged.connect(self._setup_changed)
        self.audio_ready.stateChanged.connect(self._audio_readiness_changed)

    @property
    def setup_valid(self) -> bool:
        try:
            self.resolved_setup()
            self._validate_identity()
            return not (
                self._base_resolved
                and self._base_resolved.config.presentation.audio.enabled
                and not self.audio_ready.isChecked()
            )
        except Exception:
            return False

    def set_service_state(self, text: str, can_create: bool) -> None:
        self.service_label.setText(text)
        self._service_can_create = can_create
        self._refresh_availability()

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.create_button.setEnabled(False)
        else:
            self._refresh_availability()

    def load_configuration(self) -> None:
        previous_context = self._audio_context_for_confirmation
        try:
            resolved = load_experiment(Path(self.config_edit.text()).expanduser())
            self._base_resolved = resolved
            self.config_edit.setText(str(resolved.config_path))
            self.device_edit.setText(str(resolved.device_path))
            self.seed_spin.setValue(resolved.config.random_seed)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(resolved.output_root))
            if previous_context and previous_context != self.audio_context():
                self.audio_ready.setChecked(False)
            self.refresh_preview()
        except Exception as exc:
            self._base_resolved = None
            self.preview_text.setPlainText(f"Configuration error:\n{exc}")
            self.warning_label.setText("Resolve the configuration error before starting.")
            self.warning_label.setStyleSheet("color: #b00020;")
            self._refresh_availability()

    def resolved_setup(self) -> ResolvedExperiment:
        if self._base_resolved is None:
            raise ValueError("no valid experiment configuration is loaded")
        device_text = self.device_edit.text().strip()
        mode = self.window_mode_combo.currentData()
        target = self.selected_subject_display()
        return resolve_session_setup(
            self.config_edit.text().strip(),
            device_profile_path=device_text or None,
            random_seed=self.seed_spin.value(),
            screen_index=target.psychopy_index if target else None,
            window_mode=SubjectWindowMode(mode) if mode else None,
        )

    def refresh_preview(self) -> None:
        try:
            resolved = self.resolved_setup()
            target = self.selected_subject_display()
            display_summary = (
                display_target_label(target)
                if target is not None
                else (
                    "Session configuration — PsychoPy screen "
                    f"{resolved.config.presentation.psychopy.screen_index}"
                )
            )
            self.preview_text.setPlainText(
                render_preview(resolved) + f"\nSubject display: {display_summary}"
            )
            montage = ", ".join(channel.label for channel in resolved.device.eeg_channels)
            self.montage_label.setText(
                f"{montage}; reference: {resolved.device.reference}; "
                f"ground: {resolved.device.ground}"
            )
            warnings: list[str] = []
            if len(self._subject_displays) < 2:
                warnings.append(
                    "Only one display detected; subject and experimenter views may share it."
                )
            if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
                warnings.append("Audio is enabled but readiness has not been confirmed.")
            if resolved.device.backend == "lsl":
                warnings.append("The configured LSL stream must be publishing before connection.")
            elif resolved.device.backend == "cyton":
                warnings.append("Cyton hardware and serial-port configuration are required.")
            self.warning_label.setText(
                "Warnings:\n- " + "\n- ".join(warnings)
                if warnings
                else "No setup warnings."
            )
            self.warning_label.setStyleSheet(
                "color: #9a6700;" if warnings else "color: #2e7d32;"
            )
        except Exception as exc:
            self.preview_text.setPlainText(f"Setup error:\n{exc}")
            self.warning_label.setText("Resolve the setup error before starting.")
            self.warning_label.setStyleSheet("color: #b00020;")
        self._refresh_availability()

    def create_payload(self) -> CreateSessionPayload:
        resolved = self.resolved_setup()
        participant, label = self._validate_identity()
        if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
            raise ValueError("confirm audio readiness before recording")
        mode = self.window_mode_combo.currentData()
        target = self.selected_subject_display()
        output = self.output_edit.text().strip()
        return CreateSessionPayload(
            config_path=str(resolved.config_path),
            participant_id=participant,
            session_label=label,
            output_root=output or None,
            screen_index=target.psychopy_index if target else None,
            subject_display=target,
            window_mode=SubjectWindowMode(mode) if mode else None,
            device_profile_path=str(resolved.device_path),
            random_seed=resolved.config.random_seed,
        )

    def selected_subject_display(self) -> SubjectDisplayTargetPayload | None:
        value = self.subject_screen_combo.currentData()
        return (
            SubjectDisplayTargetPayload.model_validate(value)
            if value is not None
            else None
        )

    def reset_defaults(self) -> None:
        self.participant_edit.setText(self.initial_participant)
        self.session_label_edit.setText(self.initial_session_label)
        self.config_edit.setText(str(self.initial_config))
        self.output_edit.setText(
            str(self.initial_output_root.expanduser().resolve())
            if self.initial_output_root
            else ""
        )
        self.subject_screen_combo.setCurrentIndex(0)
        self.window_mode_combo.setCurrentIndex(0)
        self.audio_ready.setChecked(False)
        self._audio_context_for_confirmation = None
        self.load_configuration()

    def audio_context(self) -> str:
        return json.dumps(
            [
                str(Path(self.config_edit.text()).expanduser().resolve()),
                str(Path(self.device_edit.text()).expanduser().resolve()),
            ],
            separators=(",", ":"),
        )

    def _validate_identity(self) -> tuple[str, str | None]:
        participant = self.participant_edit.text().strip()
        label = self.session_label_edit.text().strip() or None
        if not IDENTIFIER_PATTERN.fullmatch(participant):
            raise ValueError(
                "participant ID must be 1-64 letters, digits, underscores, or hyphens"
            )
        if label is not None and not IDENTIFIER_PATTERN.fullmatch(label):
            raise ValueError(
                "session label must be 1-64 letters, digits, underscores, or hyphens"
            )
        return participant, label

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Experiment configuration", self.config_edit.text(), "YAML (*.yaml *.yml)"
        )
        if path:
            self.config_edit.setText(path)
            self.load_configuration()

    def _browse_device(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Device profile", self.device_edit.text(), "YAML (*.yaml *.yml)"
        )
        if path:
            self.device_edit.setText(path)
            self._setup_changed()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Session output directory", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _setup_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if (
            self._audio_context_for_confirmation
            and self._audio_context_for_confirmation != self.audio_context()
        ):
            self.audio_ready.setChecked(False)
        self.refresh_preview()

    def _audio_readiness_changed(self, state: int) -> None:
        self._audio_context_for_confirmation = (
            self.audio_context() if state == Qt.CheckState.Checked.value else None
        )
        self.refresh_preview()

    def _refresh_availability(self, *_args) -> None:  # type: ignore[no-untyped-def]
        self.create_button.setEnabled(self._service_can_create and self.setup_valid)
