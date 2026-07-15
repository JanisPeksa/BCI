"""Experimenter setup, monitoring, and recovery interface."""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QCloseEvent, QPainter, QPainterPath, QPen
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
    QTableWidget,
    QTableWidgetItem,
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
from imagined_speech.operator import OperatorCommand, OperatorCommandStatus
from imagined_speech.preview import render_preview
from imagined_speech.runtime import SessionRuntime, SessionRuntimeState
from imagined_speech.subject_ui import SubjectWindow


class EEGTraceWidget(QWidget):
    """Dependency-free scrolling EEG trace renderer for operator monitoring."""

    COLORS = (
        "#5cc8ff",
        "#ff8a65",
        "#81c784",
        "#ce93d8",
        "#ffd54f",
        "#4dd0e1",
        "#f48fb1",
        "#aed581",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(280)
        self._samples: tuple[tuple[float, ...], ...] = ()
        self._indexes: tuple[int, ...] = ()
        self._labels: tuple[str, ...] = ()

    def set_data(
        self,
        samples: tuple[tuple[float, ...], ...],
        indexes: tuple[int, ...],
        labels: tuple[str, ...],
    ) -> None:
        self._samples = samples
        self._indexes = indexes
        self._labels = labels
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        if not self._samples or not self._indexes:
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for EEG samples")
            return

        left = 48
        right = max(left + 1, self.width() - 8)
        channel_height = self.height() / len(self._indexes)
        stride = max(1, len(self._samples) // max(1, right - left))
        sampled = self._samples[::stride]
        for channel_position, source_index in enumerate(self._indexes):
            center = (channel_position + 0.5) * channel_height
            painter.setPen(QPen(QColor("#263746"), 1))
            painter.drawLine(left, int(center), right, int(center))
            painter.setPen(QColor(self.COLORS[channel_position % len(self.COLORS)]))
            label = self._labels[channel_position] if channel_position < len(self._labels) else str(source_index)
            painter.drawText(5, int(center + 4), label)
            values = [row[source_index] for row in sampled if source_index < len(row)]
            if len(values) < 2:
                continue
            center_value = sum(values) / len(values)
            peak = max(max(abs(value - center_value) for value in values), 1.0)
            scale = channel_height * 0.38 / peak
            path = QPainterPath()
            for index, value in enumerate(values):
                x = left + index * (right - left) / max(1, len(values) - 1)
                y = center - (value - center_value) * scale
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(QColor(self.COLORS[channel_position % len(self.COLORS)]), 1.2))
            painter.drawPath(path)


class ExperimenterWindow(QMainWindow):
    def __init__(
        self,
        initial_config: Path,
        *,
        participant: str = "P001",
        output_root: Path | None = None,
        session_label: str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Imagined Speech — Experimenter")
        self.resize(1440, 900)
        self.runtime: SessionRuntime | None = None
        self.subject_window: SubjectWindow | None = None
        self._base_resolved: ResolvedExperiment | None = None
        self._last_event_count = -1
        self._last_command_count = -1
        self._startup_thread: threading.Thread | None = None
        self._startup_error: str | None = None
        self._startup_error_shown = False
        self._close_after_finalize = False

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.setup_page = self._build_setup_page(
            initial_config, participant, output_root, session_label
        )
        self.live_page = self._build_live_page()
        self.pages.addWidget(self.setup_page)
        self.pages.addWidget(self.live_page)

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._load_configuration()

    def _build_setup_page(
        self,
        initial_config: Path,
        participant: str,
        output_root: Path | None,
        session_label: str | None,
    ) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        title = QLabel("Session setup")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        root.addWidget(title)

        form_group = QGroupBox("Identity and protocol")
        form = QFormLayout(form_group)
        self.participant_edit = QLineEdit(participant)
        self.session_label_edit = QLineEdit(session_label or "RUN001")
        self.config_edit = QLineEdit(str(initial_config.resolve()))
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_edit)
        config_browse = QPushButton("Browse…")
        config_browse.clicked.connect(self._browse_config)
        config_row.addWidget(config_browse)
        config_load = QPushButton("Load")
        config_load.clicked.connect(self._load_configuration)
        config_row.addWidget(config_load)
        self.device_edit = QLineEdit()
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_edit)
        device_browse = QPushButton("Browse…")
        device_browse.clicked.connect(self._browse_device)
        device_row.addWidget(device_browse)
        self.output_edit = QLineEdit(str(output_root.resolve()) if output_root else "")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_browse = QPushButton("Browse…")
        output_browse.clicked.connect(self._browse_output)
        output_row.addWidget(output_browse)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.experiment_screen_combo = QComboBox()
        self.subject_screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            label = f"{index}: {screen.name()} ({screen.size().width()}×{screen.size().height()})"
            self.experiment_screen_combo.addItem(label, index)
            self.subject_screen_combo.addItem(label, index)
        if self.subject_screen_combo.count() > 1:
            self.subject_screen_combo.setCurrentIndex(1)
        self.audio_ready = QCheckBox("Audio output and volume checked")
        self.montage_label = QLabel("—")
        self.montage_label.setWordWrap(True)
        self.experiment_screen_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_preview()
        )
        self.subject_screen_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_preview()
        )
        self.audio_ready.stateChanged.connect(lambda _state: self._refresh_preview())
        self.seed_spin.valueChanged.connect(lambda _value: self._refresh_preview())
        self.device_edit.editingFinished.connect(self._refresh_preview)

        form.addRow("Participant ID", self.participant_edit)
        form.addRow("Session label", self.session_label_edit)
        form.addRow("Protocol configuration", config_row)
        form.addRow("Device profile", device_row)
        form.addRow("Random seed", self.seed_spin)
        form.addRow("Output directory", output_row)
        form.addRow("Experimenter display", self.experiment_screen_combo)
        form.addRow("Subject display", self.subject_screen_combo)
        form.addRow("Montage", self.montage_label)
        form.addRow("Audio readiness", self.audio_ready)
        root.addWidget(form_group)

        preview_group = QGroupBox("Validated preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(260)
        self.setup_warnings = QLabel()
        self.setup_warnings.setWordWrap(True)
        preview_layout.addWidget(self.preview_text)
        preview_layout.addWidget(self.setup_warnings)
        root.addWidget(preview_group, 1)

        self.start_button = QPushButton("Connect and start recording")
        self.start_button.setMinimumHeight(44)
        self.start_button.clicked.connect(self._start_session)
        root.addWidget(self.start_button)
        return page

    def _build_live_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        header = QGridLayout()
        self.session_id_label = QLabel("Session: —")
        self.runtime_state_label = QLabel("Runtime: —")
        self.recording_state_label = QLabel("Recording: —")
        self.protocol_state_label = QLabel("Protocol: —")
        self.phase_label = QLabel("Phase: —")
        self.progress_label = QLabel("Progress: —")
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
        self.command_buttons: dict[OperatorCommand, QPushButton] = {}
        for command, label in (
            (OperatorCommand.PAUSE, "Pause"),
            (OperatorCommand.RESUME, "Resume"),
            (OperatorCommand.REPEAT_TRIAL, "Repeat trial"),
            (OperatorCommand.REPEAT_BLOCK, "Repeat block"),
            (OperatorCommand.REFIT, "Refit / note"),
            (OperatorCommand.ABORT, "Abort"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, item=command: self._command(item))
            controls.addWidget(button)
            self.command_buttons[command] = button
        self.command_buttons[OperatorCommand.ABORT].setStyleSheet(
            "background: #8b1e2d; color: white;"
        )
        root.addLayout(controls)

        center = QGridLayout()
        trace_group = QGroupBox("Live EEG (display copy only; raw recording is unchanged)")
        trace_layout = QVBoxLayout(trace_group)
        self.trace = EEGTraceWidget()
        trace_layout.addWidget(self.trace)
        center.addWidget(trace_group, 0, 0, 1, 2)

        channels_group = QGroupBox("Channel reception")
        channels_layout = QVBoxLayout(channels_group)
        self.channel_table = QTableWidget(0, 4)
        self.channel_table.setHorizontalHeaderLabels(["Channel", "Status", "Latest", "Range"])
        channels_layout.addWidget(self.channel_table)
        center.addWidget(channels_group, 1, 0)

        marker_group = QGroupBox("Recent protocol markers")
        marker_layout = QVBoxLayout(marker_group)
        self.marker_table = QTableWidget(0, 4)
        self.marker_table.setHorizontalHeaderLabels(["Seq", "Event", "Code", "Time"])
        marker_layout.addWidget(self.marker_table)
        center.addWidget(marker_group, 1, 1)

        action_group = QGroupBox("Operator command audit")
        action_layout = QVBoxLayout(action_group)
        self.action_table = QTableWidget(0, 4)
        self.action_table.setHorizontalHeaderLabels(["Seq", "Command", "Result", "Reason"])
        action_layout.addWidget(self.action_table)
        center.addWidget(action_group, 2, 0)

        health_group = QGroupBox("Acquisition and storage health")
        health_layout = QVBoxLayout(health_group)
        self.health_label = QLabel("—")
        self.health_label.setWordWrap(True)
        health_layout.addWidget(self.health_label)
        center.addWidget(health_group, 2, 1)
        root.addLayout(center, 1)

        footer = QHBoxLayout()
        self.session_path_label = QLabel("Output: —")
        self.session_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.export_button = QPushButton("Export session summary…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export_summary)
        footer.addWidget(self.session_path_label, 1)
        footer.addWidget(self.export_button)
        root.addLayout(footer)
        return page

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Experiment configuration", self.config_edit.text(), "YAML (*.yaml *.yml)")
        if path:
            self.config_edit.setText(path)
            self._load_configuration()

    def _browse_device(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Device profile", self.device_edit.text(), "YAML (*.yaml *.yml)")
        if path:
            self.device_edit.setText(path)
            self._refresh_preview()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Session output directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _load_configuration(self) -> None:
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
            self._refresh_preview()
            self.start_button.setEnabled(True)
        except Exception as exc:
            self._base_resolved = None
            self.preview_text.setPlainText(f"Configuration error:\n{exc}")
            self.setup_warnings.setText("Resolve the configuration error before starting.")
            self.setup_warnings.setStyleSheet("color: #b00020;")
            self.start_button.setEnabled(False)

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

    def _refresh_preview(self) -> None:
        try:
            resolved = self._resolved_setup()
            self.preview_text.setPlainText(render_preview(resolved))
            montage = ", ".join(channel.label for channel in resolved.device.eeg_channels)
            self.montage_label.setText(
                f"{montage}; reference: {resolved.device.reference}; ground: {resolved.device.ground}"
            )
            warnings: list[str] = []
            if self.subject_screen_combo.count() < 2:
                warnings.append("Only one display detected; subject and experimenter views will share it.")
            if (
                self.subject_screen_combo.currentData()
                == self.experiment_screen_combo.currentData()
            ):
                warnings.append("Experimenter and subject displays currently select the same screen.")
            if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
                warnings.append("Audio is enabled but readiness has not been confirmed.")
            if resolved.device.backend == "lsl":
                warnings.append("The configured LSL stream must be publishing before connection.")
            elif resolved.device.backend == "cyton":
                warnings.append("Cyton hardware and serial-port configuration are required.")
            self.setup_warnings.setText(
                "Warnings:\n• " + "\n• ".join(warnings) if warnings else "No setup warnings."
            )
            self.setup_warnings.setStyleSheet("color: #9a6700;" if warnings else "color: #2e7d32;")
        except Exception as exc:
            self.preview_text.setPlainText(f"Setup error:\n{exc}")
            self.setup_warnings.setText("Resolve the setup error before starting.")
            self.setup_warnings.setStyleSheet("color: #b00020;")

    def _start_session(self) -> None:
        try:
            resolved = self._resolved_setup()
            if resolved.config.presentation.audio.enabled and not self.audio_ready.isChecked():
                raise ValueError("confirm audio readiness before recording")
            output = Path(self.output_edit.text()).expanduser() if self.output_edit.text().strip() else None
            runtime = SessionRuntime(
                resolved,
                self.participant_edit.text().strip(),
                output_root=output,
                session_label=self.session_label_edit.text().strip() or None,
            )
            self.runtime = runtime
            self.subject_window = SubjectWindow(
                runtime.engine,
                resolved,
                auto_start=False,
                drive_engine=False,
            )
            subject_index = int(self.subject_screen_combo.currentData())
            subject_screen = QApplication.screens()[subject_index]
            self.subject_window.setGeometry(subject_screen.geometry())
            if resolved.config.presentation.full_screen:
                self.subject_window.winId()
                handle = self.subject_window.windowHandle()
                if handle is not None:
                    handle.setScreen(subject_screen)
                self.subject_window.showFullScreen()
            else:
                self.subject_window.resize(1024, 720)
                self.subject_window.show()

            experimenter_index = int(self.experiment_screen_combo.currentData())
            experimenter_screen = QApplication.screens()[experimenter_index]
            handle = self.windowHandle()
            if handle is not None:
                handle.setScreen(experimenter_screen)
            self.move(experimenter_screen.geometry().topLeft())
            self.pages.setCurrentWidget(self.live_page)
            self.session_id_label.setText(
                f"Session: {runtime.writer.session_id}  ·  Participant: {runtime.writer.participant_id}"
            )
            self.session_path_label.setText(f"Output: {runtime.session_path}")
            self._startup_thread = threading.Thread(
                target=self._start_runtime,
                name="session-connection",
                daemon=True,
            )
            self._startup_thread.start()
        except Exception as exc:
            if self.subject_window is not None:
                self.subject_window.close()
            QMessageBox.critical(self, "Cannot start session", str(exc))

    def _start_runtime(self) -> None:
        assert self.runtime is not None
        try:
            self.runtime.start()
        except Exception as exc:
            self._startup_error = str(exc)

    def _tick(self) -> None:
        if self.runtime is None:
            return
        if self._startup_error and not self._startup_error_shown:
            self._startup_error_shown = True
            QMessageBox.critical(
                self,
                "Session connection failed",
                f"{self._startup_error}\n\nPartial package: {self.runtime.session_path}",
            )
        try:
            self.runtime.tick()
        except Exception as exc:
            self.runtime_state_label.setText(f"Runtime error: {exc}")
        self._render_live()
        if self._close_after_finalize and self.runtime.state in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            self.close()

    def _render_live(self) -> None:
        assert self.runtime is not None
        runtime = self.runtime
        view = runtime.engine.view_state()
        snapshot = runtime.acquisition_snapshot()
        self.runtime_state_label.setText(f"Runtime: {runtime.state.value}")
        self.recording_state_label.setText(
            f"Recording: {'active' if snapshot.running else 'stopped'} · {snapshot.sample_count} samples"
        )
        self.protocol_state_label.setText(f"Protocol: {view.run_state.value}")
        self.phase_label.setText(
            f"Phase: {view.screen} · {view.remaining_seconds:.1f}s remaining"
        )
        if view.trial_number is None:
            self.progress_label.setText("Progress: rest/break")
        else:
            self.progress_label.setText(
                f"Progress: {view.block_type} block {view.block_number}/{view.block_count}, "
                f"trial {view.trial_number}/{view.trial_count}"
            )

        indexes = tuple(
            int(value)
            for value in runtime.acquisition.backend.metadata.get(
                "eeg_channel_indexes",
                range(len(runtime.resolved.device.eeg_channels)),
            )
        )
        labels = tuple(channel.label for channel in runtime.resolved.device.eeg_channels)
        self.trace.set_data(snapshot.recent_samples, indexes, labels)
        self._render_channels(snapshot.recent_samples, indexes, labels)
        self._render_markers()
        self._render_actions()

        raw_path = runtime.session_path / "eeg_raw.csv"
        raw_size = raw_path.stat().st_size if raw_path.is_file() else 0
        free = shutil.disk_usage(runtime.session_path).free
        self.health_label.setText(
            f"Latest health: {snapshot.last_health_kind} ({snapshot.last_health_severity})\n"
            f"Dropped samples: {snapshot.dropped_samples}; timestamp/sequence gaps: "
            f"{snapshot.timestamp_discontinuities}; read/write errors: "
            f"{snapshot.read_errors}/{snapshot.write_errors}\n"
            f"Raw file: {raw_size / 1_048_576:.2f} MiB; free storage: {free / 1_073_741_824:.1f} GiB"
        )

        running = runtime.engine.state == RunState.RUNNING
        paused = runtime.engine.state == RunState.PAUSED
        in_trial = runtime.engine.current_action is not None and runtime.engine.current_action.context.trial_id is not None
        self.command_buttons[OperatorCommand.PAUSE].setEnabled(running)
        self.command_buttons[OperatorCommand.RESUME].setEnabled(paused)
        self.command_buttons[OperatorCommand.REPEAT_TRIAL].setEnabled((running or paused) and in_trial)
        self.command_buttons[OperatorCommand.REPEAT_BLOCK].setEnabled((running or paused) and in_trial)
        self.command_buttons[OperatorCommand.REFIT].setEnabled(running or paused)
        self.command_buttons[OperatorCommand.ABORT].setEnabled(running or paused)
        self.export_button.setEnabled(runtime.validation_report is not None)

    def _render_channels(
        self,
        samples: tuple[tuple[float, ...], ...],
        indexes: tuple[int, ...],
        labels: tuple[str, ...],
    ) -> None:
        self.channel_table.setRowCount(len(indexes))
        recent = samples[-250:]
        for row, (source_index, label) in enumerate(zip(indexes, labels, strict=False)):
            values = [sample[source_index] for sample in recent if source_index < len(sample)]
            if values:
                span = max(values) - min(values)
                status = "receiving" if span > 1e-9 else "flat"
                latest = f"{values[-1]:.2f}"
                range_text = f"{span:.2f}"
            else:
                status, latest, range_text = "no data", "—", "—"
            for column, value in enumerate((label, status, latest, range_text)):
                self.channel_table.setItem(row, column, QTableWidgetItem(value))

    def _render_markers(self) -> None:
        assert self.runtime is not None
        events = self.runtime.event_memory.events
        if len(events) == self._last_event_count:
            return
        self._last_event_count = len(events)
        recent = events[-12:]
        self.marker_table.setRowCount(len(recent))
        for row, event in enumerate(recent):
            values = (
                str(event.sequence_number),
                event.event_type.value,
                str(event.marker_code),
                f"{event.monotonic_seconds:.3f}",
            )
            for column, value in enumerate(values):
                self.marker_table.setItem(row, column, QTableWidgetItem(value))

    def _render_actions(self) -> None:
        assert self.runtime is not None
        records = self.runtime.operator_records
        if len(records) == self._last_command_count:
            return
        self._last_command_count = len(records)
        recent = records[-10:]
        self.action_table.setRowCount(len(recent))
        for row, record in enumerate(recent):
            values = (
                str(record.sequence_number),
                record.command.value,
                record.status.value,
                record.reason,
            )
            for column, value in enumerate(values):
                self.action_table.setItem(row, column, QTableWidgetItem(value))

    def _command(self, command: OperatorCommand) -> None:
        if self.runtime is None:
            return
        note: str | None = None
        if command == OperatorCommand.REFIT:
            note, accepted = QInputDialog.getText(
                self,
                "Refit note",
                "Describe the electrode/headset adjustment:",
            )
            if not accepted:
                return
        if command == OperatorCommand.ABORT:
            answer = QMessageBox.question(
                self,
                "Abort session",
                "Abort the protocol and finalize the partial recording?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            record = self.runtime.execute(command, note=note)
            if record.status == OperatorCommandStatus.REJECTED:
                QMessageBox.warning(self, "Command rejected", record.reason)
        except Exception as exc:
            QMessageBox.critical(self, "Command error", str(exc))

    def _export_summary(self) -> None:
        if self.runtime is None or self.runtime.validation_report is None:
            return
        default = self.runtime.session_path.parent / f"{self.runtime.session_path.name}-summary.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export session summary", str(default), "JSON (*.json)")
        if not path:
            return
        report = self.runtime.validation_report
        summary = {
            "schema_version": 1,
            "session_id": report.session_id,
            "status": report.status,
            "participant_id": self.runtime.writer.participant_id,
            "session_label": self.runtime.writer.session_label,
            "experiment_id": self.runtime.resolved.config.experiment_id,
            "device_profile": self.runtime.resolved.device.profile_id,
            "session_path": str(report.session_path),
            "event_count": report.event_count,
            "operator_command_count": report.operator_command_count,
            "completed_trial_count": report.trial_count,
            "completed_phase_count": report.phase_count,
            "sample_count": report.sample_count,
            "warnings": report.warnings,
            "qc_status": "not implemented until roadmap Milestone 5",
        }
        Path(path).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._startup_thread is not None and self._startup_thread.is_alive():
            QMessageBox.information(
                self,
                "Connection in progress",
                "Wait for the bounded device connection attempt to finish before closing.",
            )
            event.ignore()
            return
        if self.runtime is not None and self.runtime.state not in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            answer = QMessageBox.question(
                self,
                "Close experimenter",
                "Close the application and finalize the current recording?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.runtime.engine.state in {RunState.RUNNING, RunState.PAUSED}:
                self.runtime.execute(
                    OperatorCommand.ABORT,
                    note="Experimenter application closed",
                )
                self._close_after_finalize = True
                event.ignore()
                return
            if self.runtime.state == SessionRuntimeState.POST_ROLL:
                self._close_after_finalize = True
                event.ignore()
                return
            self.runtime.close()
        if self.subject_window is not None:
            self.subject_window.close()
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
