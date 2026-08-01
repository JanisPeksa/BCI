from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.runtime.coordinator import SessionCoordinator
from ssvep_bci.ui.subject_window import SubjectWindow
from ssvep_bci.ui.theme import apply_forced_theme


def run_application(
    resolved: ResolvedExperiment,
    participant_id: str,
    session_label: str | None,
    *,
    windowed: bool = False,
    verification: bool = False,
) -> int:
    surface = QSurfaceFormat()
    surface.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    surface.setSwapInterval(1)
    surface.setSamples(0)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication.instance() or QApplication([])
    apply_forced_theme(app)
    screens = app.screens()
    screen_index = resolved.config.presentation.screen_index
    if screen_index >= len(screens):
        raise ValueError(
            f"screen index {screen_index} unavailable; detected {len(screens)} screen(s)"
        )
    refresh = screens[screen_index].refreshRate()
    if refresh > 0:
        invalid = [
            stimulus for stimulus in resolved.config.stimuli
            if stimulus.frequency_hz >= refresh / 2
        ]
        if invalid:
            details = ", ".join(
                f"{stimulus.id}={stimulus.frequency_hz:g} Hz" for stimulus in invalid
            )
            raise ValueError(
                f"stimulus frequencies violate Nyquist for {refresh:g} Hz display: {details}"
            )
    coordinator = SessionCoordinator(
        resolved,
        participant_id,
        session_label,
        verification=verification,
    )
    window = SubjectWindow(coordinator, resolved)
    if resolved.config.presentation.hide_cursor:
        window.setCursor(QCursor(Qt.CursorShape.BlankCursor))
    if windowed or resolved.config.presentation.window_mode.value == "windowed":
        window.resize(1000, 700)
        window.show()
    else:
        window.setScreen(screens[screen_index])
        window.showFullScreen()
    window.begin()
    return app.exec()
