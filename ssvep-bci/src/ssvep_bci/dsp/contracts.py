from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from ssvep_bci.config.models import StrictModel

if TYPE_CHECKING:
    from ssvep_bci.dsp.windows import WindowRequest


@dataclass(frozen=True)
class StimulusWindow:
    window_id: str
    presentation_id: str
    trial_id: str
    stimulus_id: str
    target_frequency_hz: float
    candidate_frequencies_hz: tuple[float, ...]
    onset_monotonic_timestamp: float
    offset_monotonic_timestamp: float
    analysis_start_monotonic_timestamp: float
    analysis_end_monotonic_timestamp: float
    sampling_rate_hz: float
    channel_names: tuple[str, ...]
    eeg: NDArray[np.float64]
    aligned_monotonic_timestamps: NDArray[np.float64]
    expected_sample_count: int
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.eeg.ndim != 2:
            raise ValueError("window EEG must have shape (samples, channels)")
        if self.eeg.shape[0] != self.aligned_monotonic_timestamps.shape[0]:
            raise ValueError("window timestamps must match EEG sample count")
        if self.eeg.shape[1] != len(self.channel_names):
            raise ValueError("window channel names must match EEG width")
        self.eeg.setflags(write=False)
        self.aligned_monotonic_timestamps.setflags(write=False)


class ProcessingStatus(StrEnum):
    PROCESSED = "processed"
    DISABLED = "disabled"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID_INPUT = "invalid_input"
    FILTER_ERROR = "filter_error"
    MODEL_ERROR = "model_error"


class ProcessingResult(StrictModel):
    schema_version: int = 1
    window_id: str
    presentation_id: str
    status: ProcessingStatus
    processor: str
    elapsed_seconds: float = Field(ge=0)
    sample_count: int = Field(ge=0)
    channel_names: tuple[str, ...]
    candidate_frequencies_hz: tuple[float, ...]
    correlations: tuple[tuple[float, ...], ...] = ()
    scores: tuple[float, ...] = ()
    predicted_index: int | None = None
    predicted_frequency_hz: float | None = None
    diagnostics: tuple[str, ...] = ()


class DspTransport(Protocol):
    """Transport-neutral DSP lifecycle used by the coordinator."""

    def start(self) -> None: ...

    def submit(self, request: "WindowRequest") -> None: ...

    def stop(self) -> None: ...
