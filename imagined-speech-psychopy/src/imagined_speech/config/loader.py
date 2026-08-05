"""YAML loading, path resolution, and presentation-asset validation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from imagined_speech.config.legacy import reject_legacy_live_configuration
from imagined_speech.config.models import (
    ConfigurationError,
    DeviceProfile,
    ExperimentConfig,
    ResolvedExperiment,
    StrictModel,
    SubjectWindowMode,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path} must contain a YAML mapping")
    return data


def _validate_model(model_type: type[StrictModel], data: dict[str, Any], path: Path):
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration in {path}:\n{exc}") from exc


def _resolve_path(value: Path, base_dir: Path) -> Path:
    return (value if value.is_absolute() else base_dir / value).resolve()


def load_device_profile(path: str | Path) -> DeviceProfile:
    device_path = Path(path).expanduser().resolve()
    device = _validate_model(DeviceProfile, _load_yaml(device_path), device_path)
    assert isinstance(device, DeviceProfile)
    return device


def load_experiment(path: str | Path) -> ResolvedExperiment:
    config_path = Path(path).expanduser().resolve()
    config_data = _load_yaml(config_path)
    reject_legacy_live_configuration(config_data, config_path)
    config = _validate_model(ExperimentConfig, config_data, config_path)
    assert isinstance(config, ExperimentConfig)

    device_path = _resolve_path(config.device_profile, config_path.parent)
    device = load_device_profile(device_path)
    assets: dict[str, dict[str, Path]] = {}
    presentation_assets: dict[str, Path] = {}
    missing_assets: list[Path] = []
    for stimulus in config.stimuli:
        resolved: dict[str, Path] = {}
        if stimulus.image is not None:
            image_path = _resolve_path(stimulus.image, config_path.parent)
            resolved["image"] = image_path
            if not image_path.is_file():
                missing_assets.append(image_path)
        if stimulus.audio is not None:
            audio_path = _resolve_path(stimulus.audio, config_path.parent)
            resolved["audio"] = audio_path
            if config.presentation.audio.enabled and not audio_path.is_file():
                missing_assets.append(audio_path)
        elif config.presentation.audio.enabled and config.presentation.audio.require_all_stimuli:
            raise ConfigurationError(
                f"audio is required but stimulus '{stimulus.id}' has no audio path"
            )
        assets[stimulus.id] = resolved

    if config.presentation.speaking_image is not None:
        speaking_image = _resolve_path(
            config.presentation.speaking_image, config_path.parent
        )
        presentation_assets["speaking_image"] = speaking_image
        if not speaking_image.is_file():
            missing_assets.append(speaking_image)

    if missing_assets:
        paths = "\n".join(f"- {asset}" for asset in missing_assets)
        raise ConfigurationError(f"referenced assets do not exist:\n{paths}")
    return ResolvedExperiment(
        config=config,
        config_path=config_path,
        device=device,
        device_path=device_path,
        assets=assets,
        presentation_assets=presentation_assets,
    )


def resolve_session_setup(
    config_path: str | Path,
    *,
    device_profile_path: str | Path | None = None,
    random_seed: int | None = None,
    screen_index: int | None = None,
    window_mode: SubjectWindowMode | None = None,
) -> ResolvedExperiment:
    """Load and fully validate one operator-selected session setup."""

    resolved = load_experiment(config_path)
    selected_device_path = (
        Path(device_profile_path).expanduser().resolve()
        if device_profile_path is not None
        else resolved.device_path
    )
    device = load_device_profile(selected_device_path)
    config_data = resolved.config.model_dump(mode="python")
    config_data["device_profile"] = selected_device_path
    if random_seed is not None:
        config_data["random_seed"] = random_seed
    psychopy_data = dict(config_data["presentation"]["psychopy"])
    if screen_index is not None:
        psychopy_data["screen_index"] = screen_index
    if window_mode is not None:
        psychopy_data["window_mode"] = window_mode
    config_data["presentation"]["psychopy"] = psychopy_data
    config = _validate_model(ExperimentConfig, config_data, resolved.config_path)
    assert isinstance(config, ExperimentConfig)
    return replace(
        resolved,
        config=config,
        device=device,
        device_path=selected_device_path,
    )
