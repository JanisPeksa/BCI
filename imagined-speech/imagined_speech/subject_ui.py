"""Full-screen subject display driven exclusively by protocol view state."""

from __future__ import annotations

import math

from PyQt6.QtCore import QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeyEvent, QPixmap, QResizeEvent
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from imagined_speech.config import Phase, ResolvedExperiment, SubjectWindowMode
from imagined_speech.engine import (
    ProtocolEngine,
    RunState,
    TERMINAL_STATES,
    VirtualClock,
)
from imagined_speech.events import EventSource
from imagined_speech.experimenter_settings import ExperimenterSettingsStore
from imagined_speech.window_placement import SubjectWindowPlacementController


class SubjectWindow(QWidget):
    aboutToClose = pyqtSignal()

    def __init__(
        self,
        engine: ProtocolEngine,
        resolved: ResolvedExperiment,
        virtual_step_ms: int = 250,
        *,
        auto_start: bool = True,
        drive_engine: bool = True,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.resolved = resolved
        self.virtual_step_ms = virtual_step_ms
        self.drive_engine = drive_engine
        self._last_step_id: str | None = None
        self._terminal_close_scheduled = False
        self._source_pixmap: QPixmap | None = None

        self.setWindowTitle("Imagined Speech")
        self.setStyleSheet("background: #0b0d12; color: #f5f7fa;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(64, 48, 64, 48)
        layout.setSpacing(24)

        self.progress_label = QLabel()
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setStyleSheet("font-size: 22px; color: #aeb6c4;")

        self.headline_label = QLabel()
        self.headline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.headline_label.setWordWrap(True)
        self.headline_label.setStyleSheet("font-size: 80px; font-weight: 600;")

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(1)

        self.instruction_label = QLabel()
        self.instruction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setStyleSheet("font-size: 30px; color: #d5d9e0;")

        self.countdown_label = QLabel()
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_label.setStyleSheet("font-size: 34px; color: #9dc1ff;")

        layout.addWidget(self.progress_label)
        layout.addStretch(1)
        layout.addWidget(self.headline_label)
        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.instruction_label)
        layout.addWidget(self.countdown_label)
        layout.addStretch(1)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(resolved.config.presentation.audio.volume)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)

        self.timer = QTimer(self)
        self.timer.setInterval(
            virtual_step_ms if isinstance(engine.clock, VirtualClock) else 50
        )
        self.timer.timeout.connect(self._on_timer)

        if auto_start:
            self.engine.start()
        self._render()
        self.timer.start()

    def _on_timer(self) -> None:
        if self.drive_engine and self.engine.state == RunState.RUNNING:
            if isinstance(self.engine.clock, VirtualClock):
                self.engine.clock.advance(self.engine.remaining_seconds)
            self.engine.tick()
        self._render()

    def _render(self) -> None:
        view = self.engine.view_state()
        self.headline_label.setText(view.headline)
        self.instruction_label.setText(view.instruction)

        if self.resolved.config.presentation.show_countdown and view.run_state in {
            RunState.RUNNING,
            RunState.PAUSED,
        }:
            self.countdown_label.setText(str(max(0, math.ceil(view.remaining_seconds))))
        else:
            self.countdown_label.clear()

        if (
            self.resolved.config.presentation.show_progress
            and view.trial_number is not None
            and view.trial_count is not None
        ):
            prefix = "Practice" if view.block_type == "practice" else "Trial"
            block = (
                ""
                if view.block_type == "practice"
                else f" · Block {view.block_number}/{view.block_count}"
            )
            self.progress_label.setText(
                f"{prefix} {view.trial_number}/{view.trial_count}{block}"
            )
        else:
            self.progress_label.clear()

        if view.step_id != self._last_step_id:
            self._last_step_id = view.step_id
            self._update_stimulus_media(view.screen, view.stimulus_id)

        if view.run_state in TERMINAL_STATES and not self._terminal_close_scheduled:
            self._terminal_close_scheduled = True
            self.timer.stop()
            QTimer.singleShot(1500, self.close)

    def _update_stimulus_media(self, screen: str, stimulus_id: str | None) -> None:
        self.media_player.stop()
        self._source_pixmap = None
        self.image_label.clear()
        if screen != Phase.STIMULUS.value or stimulus_id is None:
            return
        assets = self.resolved.assets.get(stimulus_id, {})
        image_path = assets.get("image")
        if image_path is not None:
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                self._source_pixmap = pixmap
                self._scale_image()
        audio_path = assets.get("audio")
        if self.resolved.config.presentation.audio.enabled and audio_path is not None:
            self.media_player.setSource(QUrl.fromLocalFile(str(audio_path)))
            self.media_player.play()

    def _scale_image(self) -> None:
        if self._source_pixmap is None:
            return
        available = self.image_label.size()
        self.image_label.setPixmap(
            self._source_pixmap.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_image()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.aboutToClose.emit()
        if self.engine.state in {RunState.RUNNING, RunState.PAUSED}:
            self.engine.abort(EventSource.SUBJECT_UI)
        self.timer.stop()
        self.media_player.stop()
        event.accept()


def run_subject_window(
    engine: ProtocolEngine,
    resolved: ResolvedExperiment,
    *,
    windowed: bool = False,
    screen_index: int | None = None,
    settings_store: ExperimenterSettingsStore | None = None,
) -> int:
    app = QApplication.instance() or QApplication([])
    screens = app.screens()
    selected_index = (
        resolved.config.presentation.subject_screen
        if screen_index is None
        else screen_index
    )
    if selected_index < 0 or selected_index >= len(screens):
        raise ValueError(
            f"subject screen {selected_index} is unavailable; detected {len(screens)} screen(s)"
        )

    window = SubjectWindow(engine, resolved)
    screen = screens[selected_index]
    placement = SubjectWindowPlacementController(
        settings_store or ExperimenterSettingsStore()
    )
    window.aboutToClose.connect(
        lambda: placement.capture_window(window, screen, selected_index)
    )
    placement.open_window(
        window,
        screen,
        selected_index,
        SubjectWindowMode.CENTER
        if windowed
        else resolved.config.presentation.window_mode,
    )
    return app.exec()
