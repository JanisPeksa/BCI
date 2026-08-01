"""YAML loading, path resolution, and cross-file validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from psychopy_ssvep.config.models import (
    BrainFlowConnection,
    DeviceProfile,
    ExperimentConfig,
)


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedExperiment:
    config: ExperimentConfig
    config_path: Path
    device: DeviceProfile
    device_path: Path
    output_root: Path
    assets: dict[str, Path]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must contain a YAML mapping")
    return value


def _validate(model_type, value: dict[str, Any], path: Path):
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration in {path}:\n{exc}") from exc


def _resolve(value: Path, base: Path) -> Path:
    return (value if value.is_absolute() else base / value).expanduser().resolve()


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "configs" / "synthetic.yaml"


def _config_path(path: str | Path | None) -> Path:
    if path is None:
        return default_config_path()
    candidate = Path(path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    if candidate.parent == Path("."):
        name = candidate.name if candidate.suffix else f"{candidate.name}.yaml"
        bundled = default_config_path().parent / name
        if bundled.is_file():
            return bundled
    return candidate.resolve()


def load_experiment(path: str | Path | None = None) -> ResolvedExperiment:
    config_path = _config_path(path)
    config: ExperimentConfig = _validate(ExperimentConfig, _load_yaml(config_path), config_path)
    device_path = _resolve(config.device_profile, config_path.parent)
    if not device_path.is_file():
        raise ConfigurationError(f"device profile does not exist: {device_path}")
    device: DeviceProfile = _validate(DeviceProfile, _load_yaml(device_path), device_path)

    assets: dict[str, Path] = {}
    for asset_id, asset_value in config.resources.assets.items():
        resolved = _resolve(asset_value, config_path.parent)
        if not resolved.is_file():
            raise ConfigurationError(f"asset '{asset_id}' does not exist: {resolved}")
        assets[asset_id] = resolved

    connection = device.connection
    if isinstance(connection, BrainFlowConnection) and connection.file is not None:
        source = _resolve(connection.file, device_path.parent)
        if not source.is_file():
            raise ConfigurationError(f"BrainFlow replay file does not exist: {source}")

    return ResolvedExperiment(
        config=config,
        config_path=config_path,
        device=device,
        device_path=device_path,
        output_root=_resolve(config.output.root_dir, config_path.parent),
        assets=assets,
    )
