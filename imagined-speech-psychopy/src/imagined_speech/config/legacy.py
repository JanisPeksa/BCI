"""Targeted errors for live configurations from retired schema versions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from imagined_speech.config.models import ConfigurationError


def reject_legacy_live_configuration(data: dict[str, Any], path: Path) -> None:
    version = data.get("schema_version")
    if version in {1, 2}:
        raise ConfigurationError(
            f"{path} uses retired experiment schema version {version}; migrate to "
            "schema_version: 3 with explicit protocol.experiment and "
            "protocol.practice sections"
        )
