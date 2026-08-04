"""Persistent, display-relative placement for application windows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QWidget

from imagined_speech.config import SubjectWindowMode
from imagined_speech.ui.settings import (
    WINDOW_PLACEMENT_SCHEMA_VERSION,
    ExperimenterSettingsStore,
)


SUBJECT_WINDOW_WIDTH = 1024
SUBJECT_WINDOW_HEIGHT = 720


class SavedWindowState(StrEnum):
    NORMAL = "normal"
    MAXIMIZED = "maximized"
    FULL_SCREEN = "fullscreen"


@dataclass(frozen=True)
class SubjectWindowPlacement:
    screen_name: str
    screen_index: int
    x: int
    y: int
    width: int
    height: int
    state: SavedWindowState

    @classmethod
    def from_mapping(cls, value: object) -> "SubjectWindowPlacement | None":
        if not isinstance(value, dict):
            return None
        try:
            placement = cls(
                screen_name=str(value["screen_name"]),
                screen_index=int(value["screen_index"]),
                x=int(value["x"]),
                y=int(value["y"]),
                width=int(value["width"]),
                height=int(value["height"]),
                state=SavedWindowState(str(value["state"])),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return placement if placement.width > 0 and placement.height > 0 else None


def clamp_window_rect(rect: QRect, available: QRect) -> QRect:
    """Keep a window fully reachable inside a display's available geometry."""

    width = max(1, min(rect.width(), available.width()))
    height = max(1, min(rect.height(), available.height()))
    maximum_x = available.x() + available.width() - width
    maximum_y = available.y() + available.height() - height
    x = min(max(rect.x(), available.x()), maximum_x)
    y = min(max(rect.y(), available.y()), maximum_y)
    return QRect(x, y, width, height)


def default_window_rect(available: QRect, *, top_left: bool = False) -> QRect:
    width = min(SUBJECT_WINDOW_WIDTH, available.width())
    height = min(SUBJECT_WINDOW_HEIGHT, available.height())
    x = available.x() if top_left else available.x() + (available.width() - width) // 2
    y = available.y() if top_left else available.y() + (available.height() - height) // 2
    return QRect(x, y, width, height)


class SubjectWindowPlacementController:
    """Loads, applies, and captures subject placements without session data."""

    def __init__(self, settings: ExperimenterSettingsStore) -> None:
        self.settings = settings

    def placements(self) -> list[SubjectWindowPlacement]:
        version = self.settings.int_value("window/subject_placements_schema_version")
        if version is not None and version != WINDOW_PLACEMENT_SCHEMA_VERSION:
            return []
        try:
            decoded = json.loads(str(self.settings.value("window/subject_placements", "[]")))
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(decoded, list):
            return []
        return [
            placement
            for item in decoded
            if (placement := SubjectWindowPlacement.from_mapping(item)) is not None
        ]

    def placement_for(
        self, screen: QScreen, screen_index: int
    ) -> SubjectWindowPlacement | None:
        placements = self.placements()
        exact = next(
            (
                item
                for item in placements
                if item.screen_name == screen.name() and item.screen_index == screen_index
            ),
            None,
        )
        if exact is not None:
            return exact
        if screen.name():
            named = [item for item in placements if item.screen_name == screen.name()]
            if len(named) == 1:
                return named[0]
        return next(
            (item for item in placements if item.screen_index == screen_index), None
        )

    def open_window(
        self,
        window: QWidget,
        screen: QScreen,
        screen_index: int,
        mode: SubjectWindowMode,
    ) -> None:
        window.winId()
        handle = window.windowHandle()

        available = screen.availableGeometry()
        rect = default_window_rect(
            available, top_left=mode == SubjectWindowMode.TOP_LEFT
        )
        state = SavedWindowState.NORMAL
        if mode == SubjectWindowMode.PREVIOUS_POSITION:
            saved = self.placement_for(screen, screen_index)
            if saved is not None:
                rect = clamp_window_rect(
                    QRect(
                        available.x() + saved.x,
                        available.y() + saved.y,
                        saved.width,
                        saved.height,
                    ),
                    available,
                )
                state = saved.state

        full_screen = (
            mode == SubjectWindowMode.FULL_SCREEN
            or state == SavedWindowState.FULL_SCREEN
        )
        target_geometry = screen.geometry() if full_screen else rect
        if handle is not None:
            handle.setScreen(screen)
            handle.setPosition(target_geometry.topLeft())
        window.setGeometry(target_geometry)
        if full_screen:
            window.showFullScreen()
            handle = window.windowHandle()
            if handle is not None:
                handle.setScreen(screen)
                handle.setPosition(screen.geometry().topLeft())
        elif state == SavedWindowState.MAXIMIZED:
            window.showMaximized()
        else:
            window.showNormal()

    def capture_window(
        self, window: QWidget, screen: QScreen, screen_index: int
    ) -> None:
        state_flags = window.windowState()
        if state_flags & Qt.WindowState.WindowFullScreen:
            state = SavedWindowState.FULL_SCREEN
        elif state_flags & Qt.WindowState.WindowMaximized:
            state = SavedWindowState.MAXIMIZED
        else:
            state = SavedWindowState.NORMAL

        rect = window.normalGeometry()
        if not rect.isValid() or rect.isEmpty():
            rect = window.geometry()
        available = screen.availableGeometry()
        rect = clamp_window_rect(rect, available)
        captured = SubjectWindowPlacement(
            screen_name=screen.name(),
            screen_index=screen_index,
            x=rect.x() - available.x(),
            y=rect.y() - available.y(),
            width=rect.width(),
            height=rect.height(),
            state=state,
        )

        placements = self.placements()
        retained = [
            item
            for item in placements
            if not (
                (screen.name() and item.screen_name == screen.name())
                or (
                    not screen.name()
                    and not item.screen_name
                    and item.screen_index == screen_index
                )
            )
        ]
        retained.append(captured)
        self.settings.set_value(
            "window/subject_placements_schema_version",
            WINDOW_PLACEMENT_SCHEMA_VERSION,
        )
        self.settings.set_value(
            "window/subject_placements",
            json.dumps([asdict(item) for item in retained], separators=(",", ":")),
        )
        self.settings.sync()
