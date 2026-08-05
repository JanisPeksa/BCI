"""Versioned per-user settings for the experimenter desktop application."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from importlib.resources import files

from PyQt6.QtCore import QByteArray, QSettings, QStandardPaths


SETUP_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1
WINDOW_PLACEMENT_SCHEMA_VERSION = 1


class ExperimenterSettingsStore:
    """Injectable INI-backed settings boundary; never stores session data."""

    SETUP_KEYS = (
        "setup/participant_id",
        "setup/session_label",
        "setup/config_path",
        "setup/device_path",
        "setup/output_root",
        "setup/random_seed",
        "setup/subject_screen",
        "setup/subject_window_mode",
        "setup/audio_ready",
        "setup/audio_context",
    )
    WORKSPACE_KEYS = (
        "workspace/schema_version",
        "workspace/layout_id",
        "workspace/pane_assignments",
        "workspace/splitter_sizes",
    )

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            root = Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppConfigLocation
                )
            )
            path = root / "imagined-speech-psychopy" / "experimenter_ui.ini"
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            default_ini = files("imagined_speech").joinpath(
                "resources", "experimenter_ui.ini"
            )
            self.path.write_bytes(default_ini.read_bytes())
        self._settings = QSettings(str(self.path), QSettings.Format.IniFormat)

    def value(self, key: str, default: Any = None) -> Any:
        return self._settings.value(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self._settings.setValue(key, value)

    def int_value(self, key: str) -> int | None:
        value = self.value(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def byte_array(self, key: str) -> QByteArray:
        value = self.value(key, QByteArray())
        if isinstance(value, QByteArray):
            return value
        if isinstance(value, bytes):
            return QByteArray(value)
        return QByteArray()

    def clear_setup(self) -> None:
        for key in self.SETUP_KEYS:
            self._settings.remove(key)
        self._settings.setValue("schema/version", SETUP_SCHEMA_VERSION)
        self.sync()

    def clear_workspace(self) -> None:
        for key in self.WORKSPACE_KEYS:
            self._settings.remove(key)
        self.sync()

    def sync(self) -> None:
        self._settings.sync()
