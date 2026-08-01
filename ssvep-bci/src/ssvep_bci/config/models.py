"""Strict, immutable configuration contracts."""

from __future__ import annotations

import math
import random
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Shape(StrEnum):
    CIRCLE = "circle"
    RECTANGLE = "rectangle"


class VisualAnchor(StrEnum):
    CENTER = "center"
    BOTTOM_RIGHT = "bottom_right"


class WindowMode(StrEnum):
    FULL_SCREEN = "full_screen"
    WINDOWED = "windowed"


class TargetSide(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class HorizontalLayout(StrEnum):
    EQUAL_GAPS = "equal_gaps"
    MANUAL = "manual"


class VisualConfig(StrictModel):
    shape: Shape
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)
    anchor: VisualAnchor = VisualAnchor.CENTER
    on_color: str = Field(pattern=COLOR_PATTERN)
    off_color: str = Field(pattern=COLOR_PATTERN)

    @model_validator(mode="after")
    def validate_circle(self) -> "VisualConfig":
        if self.shape == Shape.CIRCLE and self.width_px != self.height_px:
            raise ValueError("circle width_px and height_px must be equal")
        return self

    def resolve_rect(
        self, viewport_width: int, viewport_height: int
    ) -> tuple[int, int, int, int]:
        if self.anchor == VisualAnchor.BOTTOM_RIGHT:
            return (
                viewport_width - self.width_px,
                viewport_height - self.height_px,
                self.width_px,
                self.height_px,
            )
        center_x = int(viewport_width * self.center_x)
        center_y = int(viewport_height * self.center_y)
        return (
            center_x - self.width_px // 2,
            center_y - self.height_px // 2,
            self.width_px,
            self.height_px,
        )


class StimulusConfig(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    frequency_hz: float = Field(gt=0)
    phase_offset_radians: float = 0.0
    duty_cycle: float = Field(default=0.5, gt=0, lt=1)
    visual: VisualConfig

    @model_validator(mode="after")
    def validate_finite(self) -> "StimulusConfig":
        values = (self.frequency_hz, self.phase_offset_radians, self.duty_cycle)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("stimulus numeric values must be finite")
        return self


class DualStimulusCondition(StrictModel):
    target_side: TargetSide
    distractor_stimulus_id: str = Field(pattern=ID_PATTERN)


class DualStimulusConfig(StrictModel):
    target_stimulus_id: str = Field(pattern=ID_PATTERN)
    distractor_stimulus_ids: tuple[str, ...] = Field(min_length=1)
    conditions: tuple[DualStimulusCondition, ...] | None = Field(
        default=None, min_length=1
    )
    randomize_conditions: bool = True
    width_px: int = Field(default=200, gt=0)
    height_px: int = Field(default=200, gt=0)
    center_y: float = Field(default=0.5, ge=0, le=1)
    horizontal_layout: HorizontalLayout = HorizontalLayout.EQUAL_GAPS
    left_center_x: float | None = Field(default=None, ge=0, le=1)
    right_center_x: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_layout_and_conditions(self) -> "DualStimulusConfig":
        if len(set(self.distractor_stimulus_ids)) != len(
            self.distractor_stimulus_ids
        ):
            raise ValueError("distractor_stimulus_ids must be unique")
        if self.conditions is not None:
            keys = [
                (condition.target_side, condition.distractor_stimulus_id)
                for condition in self.conditions
            ]
            if len(set(keys)) != len(keys):
                raise ValueError("dual-stimulus conditions must be unique")
        manual_centers = (self.left_center_x, self.right_center_x)
        if self.horizontal_layout == HorizontalLayout.MANUAL:
            if any(value is None for value in manual_centers):
                raise ValueError(
                    "manual horizontal layout requires left_center_x and right_center_x"
                )
            assert self.left_center_x is not None
            assert self.right_center_x is not None
            if self.left_center_x >= self.right_center_x:
                raise ValueError("left_center_x must be less than right_center_x")
        elif any(value is not None for value in manual_centers):
            raise ValueError(
                "left_center_x and right_center_x require manual horizontal layout"
            )
        return self

    @property
    def resolved_conditions(self) -> tuple[DualStimulusCondition, ...]:
        if self.conditions is not None:
            return self.conditions
        return tuple(
            DualStimulusCondition(
                target_side=side,
                distractor_stimulus_id=distractor_id,
            )
            for distractor_id in self.distractor_stimulus_ids
            for side in (TargetSide.LEFT, TargetSide.RIGHT)
        )


class StimulusPositionConfig(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)


class MultiStimulusConfig(StrictModel):
    target_stimulus_id: str = Field(pattern=ID_PATTERN)
    distractor_stimulus_ids: tuple[str, ...] = Field(min_length=1)
    positions: tuple[StimulusPositionConfig, ...] = Field(min_length=2)
    randomize_conditions: bool = True
    width_px: int = Field(default=200, gt=0)
    height_px: int = Field(default=200, gt=0)

    @model_validator(mode="after")
    def validate_multi_stimulus(self) -> "MultiStimulusConfig":
        if len(set(self.distractor_stimulus_ids)) != len(
            self.distractor_stimulus_ids
        ):
            raise ValueError("multi-stimulus distractor IDs must be unique")
        if len(self.positions) != len(self.distractor_stimulus_ids) + 1:
            raise ValueError(
                "multi-stimulus positions must match the total stimulus count"
            )
        position_ids = [position.id for position in self.positions]
        if len(set(position_ids)) != len(position_ids):
            raise ValueError("multi-stimulus position IDs must be unique")
        coordinates = [
            (position.center_x, position.center_y) for position in self.positions
        ]
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("multi-stimulus position coordinates must be unique")
        return self


class ProtocolConfig(StrictModel):
    active_stimulus_id: str = Field(pattern=ID_PATTERN)
    repetitions: int = Field(ge=1)
    stimulus_sequence: tuple[str, ...] | None = Field(default=None, min_length=1)
    randomize_stimulus_sequence: bool = False
    simultaneous_stimulus_ids: tuple[str, ...] | None = Field(default=None, min_length=1)
    acquisition_pre_roll_seconds: float = Field(default=1.0, ge=0)
    initial_rest_seconds: float = Field(default=0.0, ge=0)
    pre_stimulus_seconds: float = Field(default=0.0, ge=0)
    stimulation_seconds: float = Field(gt=0)
    inter_trial_seconds: float = Field(default=0.0, ge=0)
    sequence_break_seconds: float = Field(default=0.0, ge=0)
    final_rest_seconds: float = Field(default=0.0, ge=0)
    acquisition_post_roll_seconds: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def validate_finite(self) -> "ProtocolConfig":
        for name, value in self.model_dump().items():
            if name.endswith("_seconds") and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        return self


class PresentationConfig(StrictModel):
    window_mode: WindowMode = WindowMode.FULL_SCREEN
    screen_index: int = Field(default=0, ge=0)
    background_color: str = Field(default="#000000", pattern=COLOR_PATTERN)
    theme: Literal["forced_dark"] = "forced_dark"
    hide_cursor: bool = True
    refresh_preflight_frames: int = Field(default=120, ge=30)
    require_timing_quality: bool = True
    max_frequency_error_hz: float = Field(default=0.25, gt=0)
    max_dropped_frame_fraction: float = Field(default=0.02, ge=0, lt=1)


class OutputConfig(StrictModel):
    root_dir: Path = Path("sessions")
    flush_every_batch: bool = True
    write_checksums: bool = True


class ProcessingWindowConfig(StrictModel):
    onset_offset_seconds: float = Field(default=0.25, ge=0)
    length_seconds: float = Field(default=4.5, gt=0)
    wait_timeout_seconds: float = Field(default=2.0, ge=0)


class NotchConfig(StrictModel):
    enabled: bool = True
    frequency_hz: float = Field(default=60.0, gt=0)
    quality_factor: float = Field(default=30.0, gt=0)


class FilterBankConfig(StrictModel):
    preset: Literal["legacy_2022"] = "legacy_2022"
    subbands: int = Field(default=10, ge=1, le=10)


class CcaConfig(StrictModel):
    harmonics: int = Field(default=2, ge=1)
    components: int = Field(default=1, ge=1)


class ClassifierConfig(StrictModel):
    model_path: Path | None = None
    allow_unsafe_legacy_joblib: bool = False


class ProcessingConfig(StrictModel):
    enabled: bool = False
    required: bool = False
    processor: Literal["fbcca", "fbcca_knn", "cca_knn"] = "fbcca"
    transport: Literal["worker_thread"] = "worker_thread"
    channels: tuple[str, ...] = Field(min_length=1)
    candidate_frequencies_hz: tuple[float, ...] = Field(min_length=1)
    window: ProcessingWindowConfig = ProcessingWindowConfig()
    notch: NotchConfig = NotchConfig()
    filter_bank: FilterBankConfig = FilterBankConfig()
    cca: CcaConfig = CcaConfig()
    classifier: ClassifierConfig = ClassifierConfig()

    @model_validator(mode="after")
    def validate_processing(self) -> "ProcessingConfig":
        if self.required and not self.enabled:
            raise ValueError("required processing must also be enabled")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("processing channels must be unique")
        rounded = [round(value, 6) for value in self.candidate_frequencies_hz]
        if len(set(rounded)) != len(rounded):
            raise ValueError("processing candidate frequencies must be unique")
        if any(not math.isfinite(value) or value <= 0 for value in self.candidate_frequencies_hz):
            raise ValueError("processing candidate frequencies must be positive and finite")
        if self.enabled and len(self.candidate_frequencies_hz) < 2:
            raise ValueError("enabled processing requires at least two candidate frequencies")
        needs_model = self.processor in {"fbcca_knn", "cca_knn"}
        if needs_model != (self.classifier.model_path is not None):
            raise ValueError("classifier processors require model_path and fbcca forbids it")
        return self


class MarkerConfig(StrictModel):
    session_start: int = Field(default=10, gt=0)
    session_complete: int = Field(default=11, gt=0)
    session_abort: int = Field(default=12, gt=0)
    session_failed: int = Field(default=13, gt=0)
    trial_start: int = Field(default=20, gt=0)
    trial_end: int = Field(default=21, gt=0)
    stimulus_onset_base: int = Field(default=1000, gt=0)
    stimulus_offset_base: int = Field(default=2000, gt=0)


class ResourceConfig(StrictModel):
    assets: dict[str, Path] = Field(default_factory=dict)


class ExperimentConfig(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1)
    random_seed: int = Field(ge=0)
    device_profile: Path
    protocol: ProtocolConfig
    stimuli: tuple[StimulusConfig, ...] = Field(min_length=1)
    dual_stimulus: DualStimulusConfig | None = None
    multi_stimulus: MultiStimulusConfig | None = None
    presentation: PresentationConfig = PresentationConfig()
    output: OutputConfig = OutputConfig()
    processing: ProcessingConfig
    markers: MarkerConfig = MarkerConfig()
    resources: ResourceConfig = ResourceConfig()

    @model_validator(mode="after")
    def validate_experiment(self) -> "ExperimentConfig":
        ids = [stimulus.id for stimulus in self.stimuli]
        if len(set(ids)) != len(ids):
            raise ValueError("stimulus IDs must be unique")
        frequencies = [round(stimulus.frequency_hz, 6) for stimulus in self.stimuli]
        if len(set(frequencies)) != len(frequencies):
            raise ValueError("stimulus frequencies must be unique")
        if self.protocol.active_stimulus_id not in ids:
            raise ValueError("active_stimulus_id does not reference a configured stimulus")
        sequence = self.protocol.stimulus_sequence
        if sequence is not None:
            unknown = sorted(set(sequence) - set(ids))
            if unknown:
                raise ValueError(
                    "stimulus_sequence references unknown stimulus IDs: "
                    + ", ".join(unknown)
                )
        simultaneous = self.protocol.simultaneous_stimulus_ids
        if simultaneous is not None:
            if len(set(simultaneous)) != len(simultaneous):
                raise ValueError("simultaneous_stimulus_ids must be unique")
            unknown = sorted(set(simultaneous) - set(ids))
            if unknown:
                raise ValueError(
                    "simultaneous_stimulus_ids references unknown stimulus IDs: "
                    + ", ".join(unknown)
                )
        selected_ids = sequence or (self.protocol.active_stimulus_id,)
        if self.protocol.randomize_stimulus_sequence and sequence is None:
            raise ValueError(
                "randomize_stimulus_sequence requires stimulus_sequence"
            )
        if self.dual_stimulus is not None and self.multi_stimulus is not None:
            raise ValueError("dual_stimulus and multi_stimulus are mutually exclusive")
        if self.dual_stimulus is not None:
            dual = self.dual_stimulus
            referenced = {dual.target_stimulus_id, *dual.distractor_stimulus_ids}
            unknown = sorted(referenced - set(ids))
            if unknown:
                raise ValueError(
                    "dual_stimulus references unknown stimulus IDs: "
                    + ", ".join(unknown)
                )
            if dual.target_stimulus_id in dual.distractor_stimulus_ids:
                raise ValueError(
                    "target_stimulus_id must not also be a distractor stimulus"
                )
            condition_keys = {
                (condition.target_side, condition.distractor_stimulus_id)
                for condition in dual.resolved_conditions
            }
            expected_condition_keys = {
                (side, distractor_id)
                for distractor_id in dual.distractor_stimulus_ids
                for side in (TargetSide.LEFT, TargetSide.RIGHT)
            }
            if condition_keys != expected_condition_keys:
                raise ValueError(
                    "dual-stimulus conditions must contain each target-side and "
                    "distractor combination exactly once"
                )
            if self.protocol.active_stimulus_id != dual.target_stimulus_id:
                raise ValueError(
                    "active_stimulus_id must match dual target_stimulus_id"
                )
            if sequence is not None or simultaneous is not None:
                raise ValueError(
                    "dual_stimulus cannot be combined with stimulus_sequence or "
                    "simultaneous_stimulus_ids"
                )
            selected_ids = tuple(referenced)
        if self.multi_stimulus is not None:
            multi = self.multi_stimulus
            referenced = {multi.target_stimulus_id, *multi.distractor_stimulus_ids}
            unknown = sorted(referenced - set(ids))
            if unknown:
                raise ValueError(
                    "multi_stimulus references unknown stimulus IDs: "
                    + ", ".join(unknown)
                )
            if multi.target_stimulus_id in multi.distractor_stimulus_ids:
                raise ValueError(
                    "multi target_stimulus_id must not also be a distractor stimulus"
                )
            if self.protocol.active_stimulus_id != multi.target_stimulus_id:
                raise ValueError(
                    "active_stimulus_id must match multi target_stimulus_id"
                )
            if sequence is not None or simultaneous is not None:
                raise ValueError(
                    "multi_stimulus cannot be combined with stimulus_sequence or "
                    "simultaneous_stimulus_ids"
                )
            selected_ids = tuple(referenced)
        selected = [stimulus for stimulus in self.stimuli if stimulus.id in selected_ids]
        if self.processing.enabled and not all(
            any(abs(stimulus.frequency_hz - value) <= 1e-6
                for value in self.processing.candidate_frequencies_hz)
            for stimulus in selected
        ):
            raise ValueError(
                "every stimulus_sequence frequency must be a processing candidate"
            )
        analysis_end = (
            self.processing.window.onset_offset_seconds
            + self.processing.window.length_seconds
        )
        if analysis_end > self.protocol.stimulation_seconds + 1e-9:
            raise ValueError("processing window must fit inside stimulation_seconds")
        fixed_codes = [
            self.markers.session_start,
            self.markers.session_complete,
            self.markers.session_abort,
            self.markers.session_failed,
            self.markers.trial_start,
            self.markers.trial_end,
        ]
        onset_codes = {
            self.markers.stimulus_onset_base + index for index in range(len(self.stimuli))
        }
        offset_codes = {
            self.markers.stimulus_offset_base + index for index in range(len(self.stimuli))
        }
        if (
            len(set(fixed_codes)) != len(fixed_codes)
            or onset_codes & offset_codes
            or (onset_codes | offset_codes) & set(fixed_codes)
        ):
            raise ValueError("marker codes and stimulus marker ranges must not overlap")
        return self

    @property
    def active_stimulus(self) -> StimulusConfig:
        return next(s for s in self.stimuli if s.id == self.protocol.active_stimulus_id)

    @property
    def ordered_stimulus_ids(self) -> tuple[str, ...]:
        """Return the flattened trial order, preserving legacy configs."""
        if self.dual_stimulus is not None:
            return (
                self.dual_stimulus.target_stimulus_id,
            ) * (
                self.protocol.repetitions
                * len(self.dual_stimulus.resolved_conditions)
            )
        if self.multi_stimulus is not None:
            return (
                self.multi_stimulus.target_stimulus_id,
            ) * (
                self.protocol.repetitions
                * len(self.multi_stimulus.positions)
            )
        sequence = self.protocol.stimulus_sequence or (self.protocol.active_stimulus_id,)
        if not self.protocol.randomize_stimulus_sequence:
            return sequence * self.protocol.repetitions
        generator = random.Random(self.random_seed)
        ordered: list[str] = []
        for _ in range(self.protocol.repetitions):
            cycle = list(sequence)
            generator.shuffle(cycle)
            ordered.extend(cycle)
        return tuple(ordered)


class ChannelConfig(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1)
    unit: str = Field(default="uV", min_length=1)
    source_index: int = Field(ge=0)


class SyntheticConnection(StrictModel):
    kind: Literal["synthetic"] = "synthetic"
    seed: int = Field(default=42, ge=0)
    baseline_amplitude_uv: float = Field(default=12.0, ge=0)
    ssvep_amplitude_uv: float = Field(default=18.0, ge=0)
    noise_amplitude_uv: float = Field(default=2.0, ge=0)
    poll_interval_seconds: float = Field(default=0.02, gt=0)
    read_chunk_samples: int = Field(default=1024, ge=1)


class BrainFlowConnection(StrictModel):
    kind: Literal["brainflow"] = "brainflow"
    board_id: int
    master_board_id: int | None = None
    serial_port: str | None = "auto"
    mac_address: str = ""
    ip_address: str = ""
    ip_port: int = 0
    ip_protocol: int = 0
    other_info: str = ""
    serial_number: str = ""
    timeout: int = 0
    file: Path | None = None
    streamer_params: str = ""
    ring_buffer_samples: int = Field(default=450000, ge=1024)
    poll_interval_seconds: float = Field(default=0.02, gt=0)
    read_chunk_samples: int = Field(default=1024, ge=1)


class LslConnection(StrictModel):
    kind: Literal["lsl"] = "lsl"
    stream_name: str | None = None
    stream_type: str | None = "EEG"
    source_id: str | None = None
    resolve_timeout_seconds: float = Field(default=5.0, gt=0)
    recovery_timeout_seconds: float = Field(default=2.0, gt=0)
    correction_refresh_seconds: float = Field(default=5.0, gt=0)
    poll_interval_seconds: float = Field(default=0.02, gt=0)
    read_chunk_samples: int = Field(default=1024, ge=1)

    @model_validator(mode="after")
    def validate_selector(self) -> "LslConnection":
        if not any((self.stream_name, self.stream_type, self.source_id)):
            raise ValueError("LSL connection requires name, type, or source_id")
        return self


ConnectionConfig = Annotated[
    SyntheticConnection | BrainFlowConnection | LslConnection,
    Field(discriminator="kind"),
]


class DeviceProfile(StrictModel):
    schema_version: Literal[1]
    profile_id: str = Field(pattern=ID_PATTERN)
    backend: Literal["synthetic", "brainflow", "lsl"]
    sampling_rate_hz: float = Field(gt=0)
    channels: tuple[ChannelConfig, ...] = Field(min_length=1)
    reference: str = Field(min_length=1)
    ground: str = Field(min_length=1)
    connection: ConnectionConfig

    @model_validator(mode="after")
    def validate_device(self) -> "DeviceProfile":
        if self.backend != self.connection.kind:
            raise ValueError("device backend must match connection.kind")
        ids = [channel.id for channel in self.channels]
        labels = [channel.label for channel in self.channels]
        indexes = [channel.source_index for channel in self.channels]
        if len(set(ids)) != len(ids):
            raise ValueError("device channel IDs must be unique")
        if len(set(labels)) != len(labels):
            raise ValueError("device channel labels must be unique")
        if len(set(indexes)) != len(indexes):
            raise ValueError("device channel source indexes must be unique")
        return self
