"""Versioned configuration data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigurationError(ValueError):
    """Raised when an experiment or referenced profile cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProtocolProfile(StrEnum):
    IMAGINED_ONLY = "imagined_only"
    HEARD_IMAGINED_SPOKEN = "heard_imagined_spoken"


class Phase(StrEnum):
    REST = "rest"
    STIMULUS = "stimulus"
    FIXATION = "fixation"
    THINKING = "thinking"
    PAUSE = "pause"
    SPEAKING = "speaking"


class SubjectWindowMode(StrEnum):
    FULL_SCREEN = "FULL_SCREEN"
    TOP_LEFT = "TOP_LEFT"
    CENTER = "CENTER"


class SubjectPresentationStyle(StrEnum):
    GUIDED = "guided"
    MINIMAL_PHONEME = "minimal_phoneme"


class SDRecordingLength(StrEnum):
    TEST_14_SECONDS = "test_14_seconds"
    FIVE_MINUTES = "5_minutes"
    FIFTEEN_MINUTES = "15_minutes"
    THIRTY_MINUTES = "30_minutes"
    ONE_HOUR = "1_hour"
    TWO_HOURS = "2_hours"
    FOUR_HOURS = "4_hours"
    TWELVE_HOURS = "12_hours"
    TWENTY_FOUR_HOURS = "24_hours"


SD_RECORDING_FIRMWARE_COMMANDS: dict[SDRecordingLength, str] = {
    SDRecordingLength.TEST_14_SECONDS: "a",
    SDRecordingLength.FIVE_MINUTES: "A",
    SDRecordingLength.FIFTEEN_MINUTES: "S",
    SDRecordingLength.THIRTY_MINUTES: "F",
    SDRecordingLength.ONE_HOUR: "G",
    SDRecordingLength.TWO_HOURS: "H",
    SDRecordingLength.FOUR_HOURS: "J",
    SDRecordingLength.TWELVE_HOURS: "K",
    SDRecordingLength.TWENTY_FOUR_HOURS: "L",
}


class SDCardRecordingConfig(StrictModel):
    enabled: bool = False
    duration: SDRecordingLength = SDRecordingLength.FIVE_MINUTES


class StimulusConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = Field(min_length=1)
    image: Path | None = None
    audio: Path | None = None


class PhaseConfig(StrictModel):
    duration_seconds: float = Field(gt=0)
    instruction: str = Field(min_length=1)


class ExperimentDesign(StrictModel):
    blocks: int = Field(ge=1)
    repetitions_per_stimulus: int = Field(ge=1)


class PracticeDesign(StrictModel):
    blocks: Literal[0, 1]
    stimulus_ids: tuple[str, ...] = ()
    repetitions_per_stimulus: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_enabled_state(self) -> "PracticeDesign":
        if self.blocks == 0:
            if self.stimulus_ids or self.repetitions_per_stimulus != 0:
                raise ValueError(
                    "practice with blocks=0 must have no stimulus IDs and zero repetitions"
                )
            return self
        if not self.stimulus_ids or self.repetitions_per_stimulus <= 0:
            raise ValueError(
                "practice with blocks=1 requires stimulus IDs and positive repetitions"
            )
        if len(set(self.stimulus_ids)) != len(self.stimulus_ids):
            raise ValueError("practice stimulus IDs must be unique")
        return self


class ProtocolDesign(StrictModel):
    sequence: tuple[Phase, ...] = Field(min_length=1)
    experiment: ExperimentDesign
    practice: PracticeDesign
    initial_rest_seconds: float = Field(default=0, ge=0)
    final_rest_seconds: float = Field(default=0, ge=0)
    inter_block_break_seconds: float = Field(default=0, ge=0)
    post_trial_seconds: float = Field(default=0, ge=0)


class AudioConfig(StrictModel):
    enabled: bool = False
    volume: float = Field(default=0.8, ge=0, le=1)
    require_all_stimuli: bool = False
    device: str | int | None = Field(
        default=None,
        description=(
            "Audio output device for the PTB backend, as a device index or "
            "name; None selects the system default output"
        ),
    )


class TextStyleConfig(StrictModel):
    font: str = "Arial"
    headline_height_px: float = Field(default=80, gt=0)
    instruction_height_px: float = Field(default=30, gt=0)
    progress_height_px: float = Field(default=22, gt=0)
    countdown_height_px: float = Field(default=34, gt=0)
    primary_color: str = "#f5f7fa"
    muted_color: str = "#aeb6c4"
    accent_color: str = "#9dc1ff"


class AssetSizingConfig(StrictModel):
    max_width_px: int = Field(default=900, gt=0)
    max_height_px: int = Field(default=500, gt=0)
    interpolate: bool = True


class PsychopyConfig(StrictModel):
    monitor_name: str = Field(min_length=1)
    gamma: float = Field(default=1.0, gt=0)
    refresh_rate_hz: float = Field(gt=0)
    screen_index: int = Field(default=1, ge=0)
    window_mode: SubjectWindowMode = SubjectWindowMode.FULL_SCREEN
    window_size_px: tuple[int, int] | None = None
    hide_cursor: bool = True
    use_retina: bool = True
    check_timing: bool = True
    wait_blanking: bool = True
    text: TextStyleConfig = TextStyleConfig()
    assets: AssetSizingConfig = AssetSizingConfig()
    preflight_warmup_frames: int = Field(default=10, ge=0)
    preflight_frame_count: int = Field(default=120, ge=30)
    refresh_rate_tolerance_hz: float = Field(default=0.5, ge=0)
    max_frame_interval_factor: float = Field(default=1.5, gt=1)
    max_dropped_frame_fraction: float = Field(default=0.01, ge=0, le=1)
    max_ipc_rtt_ms: float = Field(default=25, gt=0)
    frame_ack_timeout_seconds: float = Field(default=2, gt=0)
    # Legacy configuration field retained for existing experiment files. Timing
    # threshold misses are warning-only; initialization/transport errors remain fatal.
    require_timing_quality: bool = False
    audio_backend: Literal["ptb"] = "ptb"
    audio_latency_mode: Literal[1, 2, 3, 4] = 3

    @field_validator("window_mode", mode="before")
    @classmethod
    def reject_previous_position(cls, value: object) -> object:
        if value == "PREVIOUS_POSITION":
            raise ValueError(
                "PREVIOUS_POSITION is not supported by the PsychoPy subject process; "
                "use CENTER instead"
            )
        return value

    @model_validator(mode="after")
    def validate_window_size(self) -> "PsychopyConfig":
        if self.window_size_px is not None and any(value <= 0 for value in self.window_size_px):
            raise ValueError("window_size_px dimensions must be positive")
        return self


class PresentationConfig(StrictModel):
    psychopy: PsychopyConfig
    subject_style: SubjectPresentationStyle = SubjectPresentationStyle.GUIDED
    speaking_image: Path | None = None
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
    practice_start: int = 24
    practice_end: int = 25
    trial_start: int = 30
    trial_end: int = 31
    phase_start: dict[Phase, int] = {
        Phase.REST: 40,
        Phase.STIMULUS: 41,
        Phase.THINKING: 42,
        Phase.PAUSE: 43,
        Phase.SPEAKING: 44,
        Phase.FIXATION: 45,
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
        Phase.FIXATION: 65,
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
            self.practice_start,
            self.practice_end,
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
    schema_version: Literal[3]
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    profile: ProtocolProfile
    random_seed: int = Field(ge=0)
    device_profile: Path
    protocol: ProtocolDesign
    phases: dict[Phase, PhaseConfig]
    stimuli: tuple[StimulusConfig, ...] = Field(min_length=1)
    presentation: PresentationConfig
    markers: MarkerConfig = MarkerConfig()
    qc: QCConfig = QCConfig()
    output: OutputConfig = OutputConfig()

    @model_validator(mode="after")
    def validate_experiment(self) -> "ExperimentConfig":
        stimulus_ids = [stimulus.id for stimulus in self.stimuli]
        if len(set(stimulus_ids)) != len(stimulus_ids):
            raise ValueError("stimulus IDs must be unique")

        unknown_practice = set(self.protocol.practice.stimulus_ids) - set(stimulus_ids)
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
            raise ValueError(
                "phase definitions do not match protocol sequence: "
                + "; ".join(details)
            )

        if self.recorded_trials % self.protocol.experiment.blocks != 0:
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
            self.markers.practice_start,
            self.markers.practice_end,
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
        return self.protocol.sequence

    @property
    def recorded_trials(self) -> int:
        return len(self.stimuli) * self.protocol.experiment.repetitions_per_stimulus

    @property
    def practice_trials(self) -> int:
        return (
            len(self.protocol.practice.stimulus_ids)
            * self.protocol.practice.repetitions_per_stimulus
        )

    @property
    def trials_per_block(self) -> int:
        return self.recorded_trials // self.protocol.experiment.blocks

    @property
    def trial_duration_seconds(self) -> float:
        return (
            sum(self.phases[phase].duration_seconds for phase in self.phase_sequence)
            + self.protocol.post_trial_seconds
        )

    @property
    def projected_duration_seconds(self) -> float:
        timed_trials = self.recorded_trials + self.practice_trials
        return (
            self.protocol.initial_rest_seconds
            + timed_trials * self.trial_duration_seconds
            + max(0, self.protocol.experiment.blocks - 1)
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
    sd_card_recording: SDCardRecordingConfig = SDCardRecordingConfig()

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
        if self.sd_card_recording.enabled and not (
            self.backend == "cyton" and self.board_id == 2
        ):
            raise ValueError(
                "SD card recording is only supported on the Cyton+Daisy "
                "16-channel device (backend 'cyton', board_id 2)"
            )
        return self


@dataclass(frozen=True)
class ResolvedExperiment:
    config: ExperimentConfig
    config_path: Path
    device: DeviceProfile
    device_path: Path
    assets: dict[str, dict[str, Path]]
    presentation_assets: dict[str, Path]

    @property
    def output_root(self) -> Path:
        value = self.config.output.root_dir
        return (value if value.is_absolute() else self.config_path.parent / value).resolve()
