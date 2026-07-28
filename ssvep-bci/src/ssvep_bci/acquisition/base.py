from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from ssvep_bci.config.models import ChannelConfig, DeviceProfile


class AcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquisitionDescriptor:
    backend: str
    profile_id: str
    sampling_rate_hz: float
    channels: tuple[ChannelConfig, ...]
    supports_embedded_markers: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SampleBatch:
    eeg: NDArray[np.float64]
    source_timestamps: NDArray[np.float64]
    corrected_source_timestamps: NDArray[np.float64]
    aligned_monotonic_timestamps: NDArray[np.float64]
    embedded_markers: NDArray[np.float64] | None
    receipt_monotonic_timestamp: float
    receipt_wall_clock_timestamp_utc: datetime

    def __post_init__(self) -> None:
        if self.eeg.ndim != 2:
            raise ValueError("eeg must have shape (samples, channels)")
        count = self.eeg.shape[0]
        for value in (
            self.source_timestamps,
            self.corrected_source_timestamps,
            self.aligned_monotonic_timestamps,
        ):
            if value.shape != (count,):
                raise ValueError("timestamp arrays must match sample count")
        if self.embedded_markers is not None and self.embedded_markers.shape != (count,):
            raise ValueError("embedded markers must match sample count")
        for value in (
            self.eeg,
            self.source_timestamps,
            self.corrected_source_timestamps,
            self.aligned_monotonic_timestamps,
            self.embedded_markers,
        ):
            if value is not None:
                value.setflags(write=False)

    @property
    def sample_count(self) -> int:
        return self.eeg.shape[0]


@dataclass(frozen=True)
class MarkerRequest:
    event_id: str
    event_sequence: int
    event_type: str
    marker_code: int
    event_monotonic_timestamp: float
    event_wall_clock_timestamp_utc: datetime
    stimulus_frequency_hz: float | None = None
    distractor_frequency_hz: float | None = None
    stimulus_frequencies_hz: tuple[float, ...] = ()


@dataclass(frozen=True)
class MarkerReceipt:
    request: MarkerRequest
    supported: bool
    success: bool
    attempt_monotonic_timestamp: float
    error: str | None = None


class AcquisitionBackend(Protocol):
    profile: DeviceProfile

    def prepare(self) -> AcquisitionDescriptor: ...
    def start(self) -> None: ...
    def read_available(self, max_samples: int) -> SampleBatch | None: ...
    def insert_marker(self, request: MarkerRequest) -> MarkerReceipt: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
