from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.planning.models import StepKind
from ssvep_bci.runtime.commands import AbortSession, CloseRequested
from ssvep_bci.runtime.coordinator import CoordinatorState, SessionCoordinator
from ssvep_bci.runtime.view_state import ViewState
from ssvep_bci.stimuli.opengl_renderer import StimulusRenderer


def format_trial_progress(view: ViewState) -> str:
    rest_phases = {StepKind.INTER_TRIAL.value, StepKind.FINAL_REST.value}
    if (
        view.phase not in rest_phases
        or view.completed_trial_count <= 0
        or view.trial_count is None
    ):
        return ""
    return f"Trial {view.completed_trial_count}/{view.trial_count} completed"


class SubjectWindow(QWidget):
    def __init__(self, coordinator: SessionCoordinator, resolved: ResolvedExperiment) -> None:
        super().__init__()
        self.coordinator = coordinator
        self.resolved = resolved
        self._allow_close = False
        self.setWindowTitle(resolved.config.title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.renderer = StimulusRenderer(
            coordinator.clock, resolved.config.presentation.background_color, self
        )
        self.renderer.command_emitted.connect(self._execute_command)
        self.message = QLabel()
        self.message.setObjectName("messageLabel")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.status = QLabel()
        self.status.setObjectName("statusLabel")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        layout.addWidget(self.renderer, 1)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)
        coordinator.add_finalized_callback(self._on_finalized)

    def begin(self) -> None:
        self.coordinator.start()
        self.timer.start()

    def _tick(self) -> None:
        self.coordinator.tick()
        view = self.coordinator.runtime.view_state()
        self.renderer.set_view_state(view)
        self.message.setText(view.message)
        self.status.setText(format_trial_progress(view))

    def _execute_command(self, command) -> None:
        try:
            self.coordinator.execute(command)
        except Exception as exc:
            self.coordinator.runtime.fail(str(exc))

    def _on_finalized(self) -> None:
        self._allow_close = True
        self.timer.stop()
        QTimer.singleShot(250, self.close)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.coordinator.execute(AbortSession("Escape pressed"))
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close or self.coordinator.state in {
            CoordinatorState.FINALIZED, CoordinatorState.FAILED
        }:
            event.accept()
            return
        self.coordinator.execute(CloseRequested())
        event.ignore()
