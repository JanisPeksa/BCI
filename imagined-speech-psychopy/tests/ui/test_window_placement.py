import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from imagined_speech.config import SubjectWindowMode
from imagined_speech.ui.settings import ExperimenterSettingsStore
from imagined_speech.ui.window_placement import (
    SavedWindowState,
    SubjectWindowPlacementController,
    clamp_window_rect,
    default_window_rect,
)


class _Screen:
    def __init__(self, name: str, available: QRect) -> None:
        self._name = name
        self._available = available

    def name(self) -> str:
        return self._name

    def availableGeometry(self) -> QRect:
        return QRect(self._available)

    def geometry(self) -> QRect:
        return QRect(self._available)


class _WindowHandle:
    def __init__(self) -> None:
        self.screens: list[object] = []
        self.positions = []

    def setScreen(self, screen: object) -> None:
        self.screens.append(screen)

    def setPosition(self, position) -> None:  # type: ignore[no-untyped-def]
        self.positions.append(position)


class _Window:
    def __init__(self) -> None:
        self.handle = _WindowHandle()
        self.geometry: QRect | None = None
        self.full_screen = False

    def winId(self) -> int:
        return 1

    def windowHandle(self) -> _WindowHandle:
        return self.handle

    def setGeometry(self, geometry: QRect) -> None:
        self.geometry = QRect(geometry)

    def showFullScreen(self) -> None:
        self.full_screen = True


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_default_and_clamped_window_geometry() -> None:
    available = QRect(1920, 40, 1280, 680)

    assert default_window_rect(available) == QRect(2048, 40, 1024, 680)
    assert default_window_rect(available, top_left=True) == QRect(1920, 40, 1024, 680)
    assert clamp_window_rect(QRect(4000, -200, 1600, 900), available) == available


def test_full_screen_targets_the_selected_screen_native_position(tmp_path) -> None:
    controller = SubjectWindowPlacementController(
        ExperimenterSettingsStore(tmp_path / "ui.ini")
    )
    selected_screen = _Screen("SUBJECT", QRect(1920, -200, 2560, 1440))
    window = _Window()

    controller.open_window(
        window,  # type: ignore[arg-type]
        selected_screen,  # type: ignore[arg-type]
        1,
        SubjectWindowMode.FULL_SCREEN,
    )

    assert window.geometry == selected_screen.geometry()
    assert window.full_screen
    assert window.handle.screens == [selected_screen, selected_screen]
    assert window.handle.positions == [
        selected_screen.geometry().topLeft(),
        selected_screen.geometry().topLeft(),
    ]


def test_subject_placements_are_saved_per_display(tmp_path) -> None:
    app = _app()
    store = ExperimenterSettingsStore(tmp_path / "ui.ini")
    controller = SubjectWindowPlacementController(store)
    first_screen = _Screen("DISPLAY-A", QRect(0, 0, 1920, 1080))
    second_screen = _Screen("DISPLAY-B", QRect(1920, 0, 1280, 1024))
    window = QWidget()

    window.setGeometry(100, 80, 900, 700)
    controller.capture_window(window, first_screen, 0)  # type: ignore[arg-type]
    window.setGeometry(2020, 120, 800, 600)
    window.setWindowState(Qt.WindowState.WindowMaximized)
    controller.capture_window(window, second_screen, 1)  # type: ignore[arg-type]

    first = controller.placement_for(first_screen, 0)  # type: ignore[arg-type]
    second = controller.placement_for(second_screen, 1)  # type: ignore[arg-type]
    assert first is not None and (first.x, first.y, first.width, first.height) == (
        100,
        80,
        900,
        700,
    )
    assert first.state == SavedWindowState.NORMAL
    assert second is not None and (second.x, second.y) == (100, 120)
    assert second.state == SavedWindowState.MAXIMIZED

    reloaded = SubjectWindowPlacementController(
        ExperimenterSettingsStore(tmp_path / "ui.ini")
    )
    assert reloaded.placement_for(first_screen, 0) == first  # type: ignore[arg-type]
    assert reloaded.placement_for(second_screen, 1) == second  # type: ignore[arg-type]
    window.close()
    app.processEvents()


def test_previous_position_restores_state_and_invalid_schema_falls_back(tmp_path) -> None:
    app = _app()
    store = ExperimenterSettingsStore(tmp_path / "ui.ini")
    controller = SubjectWindowPlacementController(store)
    screen = app.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()
    saved = {
        "screen_name": screen.name(),
        "screen_index": 0,
        "x": 20,
        "y": 30,
        "width": min(640, available.width()),
        "height": min(480, available.height()),
        "state": "maximized",
    }
    store.set_value("window/subject_placements_schema_version", 1)
    store.set_value("window/subject_placements", json.dumps([saved]))
    store.sync()

    window = QWidget()
    controller.open_window(window, screen, 0, SubjectWindowMode.PREVIOUS_POSITION)
    app.processEvents()
    assert window.isMaximized()
    window.close()

    store.set_value("window/subject_placements_schema_version", 99)
    store.sync()
    fallback = QWidget()
    controller.open_window(fallback, screen, 0, SubjectWindowMode.PREVIOUS_POSITION)
    app.processEvents()
    assert not fallback.isMaximized()
    expected = default_window_rect(available)
    assert fallback.geometry().size() == expected.size()
    assert abs(fallback.geometry().x() - expected.x()) <= 2
    assert abs(fallback.geometry().y() - expected.y()) <= 2
    fallback.close()


@pytest.mark.parametrize(
    ("mode", "full_screen", "top_left"),
    [
        (SubjectWindowMode.FULL_SCREEN, True, False),
        (SubjectWindowMode.TOP_LEFT, False, True),
        (SubjectWindowMode.CENTER, False, False),
    ],
)
def test_explicit_window_modes_apply_configured_geometry(
    tmp_path, mode: SubjectWindowMode, full_screen: bool, top_left: bool
) -> None:
    app = _app()
    controller = SubjectWindowPlacementController(
        ExperimenterSettingsStore(tmp_path / f"{mode.value}.ini")
    )
    screen = app.primaryScreen()
    assert screen is not None
    window = QWidget()

    controller.open_window(window, screen, 0, mode)
    app.processEvents()

    assert window.isFullScreen() is full_screen
    if not full_screen:
        expected = default_window_rect(
            screen.availableGeometry(), top_left=top_left
        )
        assert window.geometry().size() == expected.size()
        assert abs(window.geometry().x() - expected.x()) <= 2
        assert abs(window.geometry().y() - expected.y()) <= 2
    window.close()
