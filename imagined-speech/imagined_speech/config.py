"""Versioned configuration models and loading helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigurationError(ValueError):
    """Raised when an experiment or referenced profile cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProtocolProfile(StrEnum):
    IMAGINED_ONLY = "imagined_only"
    FEIS_COMPARABLE = "feis_comparable"


class Phase(StrEnum):
    REST = "rest"
    STIMULUS = "stimulus"
    THINKING = "thinking"
    PAUSE = "pause"
    SPEAKING = "speaking"


PHASE_SEQUENCES: dict[ProtocolProfile, tuple[Phase, ...]] = {
    ProtocolProfile.IMAGINED_ONLY: (
        Phase.REST,
        Phase.STIMULUS,
        Phase.THINKING,
        Phase.PAUSE,
    ),
    ProtocolProfile.FEIS_COMPARABLE: (
        Phase.REST,
        Phase.STIMULUS,
        Phase.THINKING,
        Phase.SPEAKING,
    ),
}


class StimulusConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = Field(min_length=1)
    image: Path | None = None
    audio: Path | None = None


class PhaseConfig(StrictModel):
    duration_seconds: float = Field(gt=0)
    instruction: str = Field(min_length=1)


class ProtocolDesign(StrictModel):
    blocks: int = Field(ge=1)
    repetitions_per_stimulus: int = Field(ge=1)
    practice_stimulus_ids: tuple[str, ...] = ()
    practice_repetitions_per_stimulus: int = Field(default=0, ge=0)
    initial_rest_seconds: float = Field(default=0, ge=0)
    final_rest_seconds: float = Field(default=0, ge=0)
    inter_block_break_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_practice(self) -> "ProtocolDesign":
        has_stimuli = bool(self.practice_stimulus_ids)
        has_repetitions = self.practice_repetitions_per_stimulus > 0
        if has_stimuli != has_repetitions:
            raise ValueError(
                "practice stimuli and practice repetitions must either both be set or both be empty"
            )
        if len(set(self.practice_stimulus_ids)) != len(self.practice_stimulus_ids):
            raise ValueError("practice stimulus IDs must be unique")
        return self


class AudioConfig(StrictModel):
    enabled: bool = False
    volume: float = Field(default=0.8, ge=0, le=1)
    require_all_stimuli: bool = False


class PresentationConfig(StrictModel):
    full_screen: bool = True
    subject_screen: int = Field(default=1, ge=0)
    show_countdown: bool = True
    show_progress: bool = True
    audio: AudioConfig = AudioConfig()


class MarkerConfig(StrictModel):
    session_start: int = 10
    session_complete: int = 11
    session_abort: int = 12
    block_start: int = 20
    block_end: int = 21
    break_start: int = 22
    break_end: int = 23
    trial_start: int = 30
    trial_end: int = 31
    phase_start: dict[Phase, int] = {
        Phase.REST: 40,
        Phase.STIMULUS: 41,
        Phase.THINKING: 42,
        Phase.PAUSE: 43,
        Phase.SPEAKING: 44,
    }
    operator_pause: int = 50
    operator_resume: int = 51
    operator_abort: int = 52
    operator_repeat_trial: int = 53
    operator_repeat_block: int = 54
    operator_refit: int = 55
    phase_end: dict[Phase, int] = {
        Phase.REST: 60,
        Phase.STIMULUS: 61,
        Phase.THINKING: 62,
        Phase.PAUSE: 63,
        Phase.SPEAKING: 64,
    }
    stimulus_base: int = Field(default=1000, ge=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> "MarkerConfig":
        required_phases = set(Phase)
        if set(self.phase_start) != required_phases:
            raise ValueError("phase_start must define every phase")
        if set(self.phase_end) != required_phases:
            raise ValueError("phase_end must define every phase")
        fixed_codes = [
            self.session_start,
            self.session_complete,
            self.session_abort,
            self.block_start,
            self.block_end,
            self.break_start,
            self.break_end,
            self.trial_start,
            self.trial_end,
            self.operator_pause,
            self.operator_resume,
            self.operator_abort,
            self.operator_repeat_trial,
            self.operator_repeat_block,
            self.operator_refit,
            *self.phase_start.values(),
            *self.phase_end.values(),
        ]
        if any(code <= 0 for code in fixed_codes):
            raise ValueError("marker codes must be positive integers")
        if len(set(fixed_codes)) != len(fixed_codes):
            raise ValueError("fixed marker codes must be unique")
        return self


class QCConfig(StrictModel):
    enabled: bool = False
    line_frequency_hz: Literal[50, 60] = 50


class OutputConfig(StrictModel):
    root_dir: Path = Path("sessions")


class ExperimentConfig(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    profile: ProtocolProfile
    random_seed: int = Field(ge=0)
    device_profile: Path
    protocol: ProtocolDesign
    phases: dict[Phase, PhaseConfig]
    stimuli: tuple[StimulusConfig, ...] = Field(min_length=1)
    presentation: PresentationConfig = PresentationConfig()
    markers: MarkerConfig = MarkerConfig()
    qc: QCConfig = QCConfig()
    output: OutputConfig = OutputConfig()

    @model_validator(mode="after")
    def validate_experiment(self) -> "ExperimentConfig":
        stimulus_ids = [stimulus.id for stimulus in self.stimuli]
        if len(set(stimulus_ids)) != len(stimulus_ids):
            raise ValueError("stimulus IDs must be unique")

        unknown_practice = set(self.protocol.practice_stimulus_ids) - set(stimulus_ids)
        if unknown_practice:
            raise ValueError(
                "unknown practice stimulus IDs: " + ", ".join(sorted(unknown_practice))
            )

        required_phases = set(self.phase_sequence)
        configured_phases = set(self.phases)
        if configured_phases != required_phases:
            missing = sorted(phase.value for phase in required_phases - configured_phases)
            unexpected = sorted(phase.value for phase in configured_phases - required_phases)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise ValueError("phase definitions do not match profile: " + "; ".join(details))

        if self.recorded_trials % self.protocol.blocks != 0:
            raise ValueError("recorded trial count must divide evenly across blocks")

        stimulus_codes = {
            self.markers.stimulus_base + index for index in range(len(self.stimuli))
        }
        fixed_codes = set(self.markers.phase_start.values()) | set(
            self.markers.phase_end.values()
        )
        fixed_codes |= {
            self.markers.session_start,
            self.markers.session_complete,
            self.markers.session_abort,
            self.markers.block_start,
            self.markers.block_end,
            self.markers.break_start,
            self.markers.break_end,
            self.markers.trial_start,
            self.markers.trial_end,
            self.markers.operator_pause,
            self.markers.operator_resume,
            self.markers.operator_abort,
            self.markers.operator_repeat_trial,
            self.markers.operator_repeat_block,
            self.markers.operator_refit,
        }
        if stimulus_codes & fixed_codes:
            raise ValueError("stimulus marker range overlaps fixed marker codes")
        return self

    @property
    def phase_sequence(self) -> tuple[Phase, ...]:
        return PHASE_SEQUENCES[self.profile]

    @property
    def recorded_trials(self) -> int:
        return len(self.stimuli) * self.protocol.repetitions_per_stimulus

    @property
    def practice_trials(self) -> int:
        return (
            len(self.protocol.practice_stimulus_ids)
            * self.protocol.practice_repetitions_per_stimulus
        )

    @property
    def trials_per_block(self) -> int:
        return self.recorded_trials // self.protocol.blocks

    @property
    def trial_duration_seconds(self) -> float:
        return sum(self.phases[phase].duration_seconds for phase in self.phase_sequence)

    @property
    def projected_duration_seconds(self) -> float:
        timed_trials = self.recorded_trials + self.practice_trials
        return (
            self.protocol.initial_rest_seconds
            + timed_trials * self.trial_duration_seconds
            + max(0, self.protocol.blocks - 1)
            * self.protocol.inter_block_break_seconds
            + self.protocol.final_rest_seconds
        )


class ChannelConfig(StrictModel):
    board_channel: int = Field(ge=0)
    label: str = Field(min_length=1)
    position: str = Field(min_length=1)


class DeviceProfile(StrictModel):
    schema_version: Literal[1]
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    backend: Literal["synthetic", "brainflow_synthetic", "replay", "cyton", "lsl"]
    board_id: int | None = None
    sampling_rate_hz: float = Field(gt=0)
    eeg_channels: tuple[ChannelConfig, ...] = Field(min_length=1)
    reference: str = Field(min_length=1)
    ground: str = Field(min_length=1)
    connection: dict[str, str | int | float | bool | None] = {}
    pre_roll_seconds: float = Field(default=1.0, ge=0)
    post_roll_seconds: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def validate_channels(self) -> "DeviceProfile":
        board_channels = [channel.board_channel for channel in self.eeg_channels]
        labels = [channel.label for channel in self.eeg_channels]
        if len(set(board_channels)) != len(board_channels):
            raise ValueError("device board channels must be unique")
        if len(set(labels)) != len(labels):
            raise ValueError("device channel labels must be unique")
        if self.backend in {"brainflow_synthetic", "cyton", "replay"} and self.board_id is None:
            raise ValueError(f"{self.backend} device profile requires board_id")
        if self.backend == "lsl" and not (
            self.connection.get("stream_name") or self.connection.get("stream_type")
        ):
            raise ValueError("LSL device profile requires stream_name or stream_type")
        return self


@dataclass(frozen=True)
class ResolvedExperiment:
    config: ExperimentConfig
    config_path: Path
    device: DeviceProfile
    device_path: Path
    assets: dict[str, dict[str, Path]]

    @property
    def output_root(self) -> Path:
        return _resolve_path(self.config.output.root_dir, self.config_path.parent)


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
    path = value if value.is_absolute() else base_dir / value
    return path.resolve()


def load_device_profile(path: str | Path) -> DeviceProfile:
    device_path = Path(path).expanduser().resolve()
    device = _validate_model(DeviceProfile, _load_yaml(device_path), device_path)
    assert isinstance(device, DeviceProfile)
    return device


def load_experiment(path: str | Path) -> ResolvedExperiment:
    config_path = Path(path).expanduser().resolve()
    config = _validate_model(ExperimentConfig, _load_yaml(config_path), config_path)
    assert isinstance(config, ExperimentConfig)

    device_path = _resolve_path(config.device_profile, config_path.parent)
    device = load_device_profile(device_path)

    assets: dict[str, dict[str, Path]] = {}
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
        elif (
            config.presentation.audio.enabled
            and config.presentation.audio.require_all_stimuli
        ):
            raise ConfigurationError(
                f"audio is required but stimulus '{stimulus.id}' has no audio path"
            )
        assets[stimulus.id] = resolved

    if missing_assets:
        paths = "\n".join(f"- {asset}" for asset in missing_assets)
        raise ConfigurationError(f"referenced assets do not exist:\n{paths}")

    return ResolvedExperiment(
        config=config,
        config_path=config_path,
        device=device,
        device_path=device_path,
        assets=assets,
    )
