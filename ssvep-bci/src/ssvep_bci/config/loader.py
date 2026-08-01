"""YAML loading, path resolution, and cross-file validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ssvep_bci.config.models import (
    BrainFlowConnection,
    DeviceProfile,
    ExperimentConfig,
    LslConnection,
    ProcessingConfig,
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
    classifier_path: Path | None


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
    return Path(__file__).resolve().parents[1] / "resources" / "configs" / "default.yaml"


def _config_path(path: str | Path | None) -> Path:
    if path is None:
        return default_config_path()
    candidate = Path(path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    if not candidate.is_absolute() and ".." not in candidate.parts:
        bundled_root = default_config_path().parent
        bundled = bundled_root / candidate
        if not bundled.suffix:
            bundled = bundled.with_suffix(".yaml")
        if bundled.is_file():
            return bundled.resolve()

        aliases = {
            "frequency-validation": Path("frequency-validation/12-75.yaml"),
            "frequency-validation-with-distractors": Path(
                "frequency-validation/with-distractors.yaml"
            ),
        }
        alias = aliases.get(candidate.as_posix())
        if alias is not None and (bundled_root / alias).is_file():
            return (bundled_root / alias).resolve()
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

    classifier_path = None
    if config.processing.classifier.model_path is not None:
        classifier_path = _resolve(config.processing.classifier.model_path, config_path.parent)
        if not classifier_path.is_file():
            raise ConfigurationError(f"classifier model does not exist: {classifier_path}")

    connection = device.connection
    if isinstance(connection, BrainFlowConnection) and connection.file is not None:
        source = _resolve(connection.file, device_path.parent)
        if not source.is_file():
            raise ConfigurationError(f"BrainFlow replay file does not exist: {source}")

    channel_labels = {channel.label for channel in device.channels}
    missing_channels = set(config.processing.channels) - channel_labels
    if missing_channels:
        raise ConfigurationError(
            "processing channels are absent from the device profile: "
            + ", ".join(sorted(missing_channels))
        )
    nyquist = device.sampling_rate_hz / 2.0
    if config.processing.notch.enabled and config.processing.notch.frequency_hz >= nyquist:
        raise ConfigurationError("notch frequency must be below device Nyquist frequency")
    if config.processing.filter_bank.preset == "legacy_2022" and nyquist <= 100:
        raise ConfigurationError("legacy_2022 filter bank requires sampling_rate_hz > 200")
    highest_reference = max(config.processing.candidate_frequencies_hz) * config.processing.cca.harmonics
    if highest_reference >= nyquist:
        raise ConfigurationError("highest CCA harmonic must be below Nyquist frequency")
    if config.processing.cca.components > min(
        len(config.processing.channels), 2 * config.processing.cca.harmonics
    ):
        raise ConfigurationError("CCA components exceed channel/reference dimensions")

    return ResolvedExperiment(
        config=config,
        config_path=config_path,
        device=device,
        device_path=device_path,
        output_root=_resolve(config.output.root_dir, config_path.parent),
        assets=assets,
        classifier_path=classifier_path,
    )


def enable_fbtdca_verification(
    resolved: ResolvedExperiment, model_path: str | Path
) -> ResolvedExperiment:
    """Return an effective verification configuration without editing source YAML."""
    classifier_path = Path(model_path).expanduser().resolve()
    if not classifier_path.is_file():
        raise ConfigurationError(f"classifier model does not exist: {classifier_path}")
    processing_value = resolved.config.processing.model_dump(mode="python")
    processing_value.update({
        "enabled": True,
        "required": True,
        "processor": "fbtdca",
        "classifier": {
            "model_path": classifier_path,
            "allow_unsafe_legacy_joblib": False,
        },
    })
    config_value = resolved.config.model_dump(mode="python")
    config_value["processing"] = processing_value
    try:
        config = ExperimentConfig.model_validate(config_value)
    except ValidationError as exc:
        raise ConfigurationError(
            f"configuration cannot be used for FBTDCA verification:\n{exc}"
        ) from exc
    if len(config.processing.candidate_frequencies_hz) != 4:
        raise ConfigurationError("FBTDCA verification requires exactly four candidates")
    phases = tuple(stimulus.phase_offset_radians for stimulus in config.stimuli)
    if any(abs(value) > 1e-12 for value in phases):
        raise ConfigurationError("FBTDCA verification requires zero phase offsets")
    return ResolvedExperiment(
        config=config,
        config_path=resolved.config_path,
        device=resolved.device,
        device_path=resolved.device_path,
        output_root=resolved.output_root,
        assets=resolved.assets,
        classifier_path=classifier_path,
    )
