"""Targeted errors for live configurations from retired schema versions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from imagined_speech.config.models import ConfigurationError


def reject_legacy_live_configuration(data: dict[str, Any], path: Path) -> None:
    if data.get("schema_version") == 1:
        raise ConfigurationError(
            f"{path} uses experiment schema version 1 with the retired Qt-only "
            "subject presentation; migrate to schema_version: 2 and add an explicit "
            "presentation.psychopy block"
        )
