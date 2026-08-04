"""Common contracts for continuous EEG acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from imagined_speech.config import DeviceProfile


class AcquisitionError(RuntimeError):
    """Raised when an acquisition source cannot be prepared or read."""


@dataclass(frozen=True)
class SampleBatch:
    source_timestamps: tuple[float, ...]
    corrected_timestamps: tuple[float, ...]
    samples: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        count = len(self.samples)
        if len(self.source_timestamps) != count:
            raise ValueError("source timestamp count does not match sample count")
        if len(self.corrected_timestamps) != count:
            raise ValueError("corrected timestamp count does not match sample count")
        if self.samples:
            width = len(self.samples[0])
            if any(len(sample) != width for sample in self.samples):
                raise ValueError("sample rows must have a consistent channel count")

    @property
    def sample_count(self) -> int:
        return len(self.samples)


class AcquisitionBackend(Protocol):
    profile: DeviceProfile

    @property
    def channel_names(self) -> tuple[str, ...]: ...

    @property
    def supports_embedded_markers(self) -> bool: ...

    @property
    def metadata(self) -> dict[str, Any]: ...

    def prepare(self) -> None: ...

    def start(self) -> None: ...

    def read_available(self) -> SampleBatch | None: ...

    def insert_marker(self, value: int, timestamp: float) -> bool: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...
