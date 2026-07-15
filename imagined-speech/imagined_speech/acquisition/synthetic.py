"""Deterministic EEG-like source that also supports virtual protocol time."""

from __future__ import annotations

import math
import threading
from typing import Any

from imagined_speech.acquisition.base import AcquisitionError, SampleBatch
from imagined_speech.config import DeviceProfile
from imagined_speech.engine import ProtocolClock


def synthetic_eeg_sample(
    sample_index: int, sampling_rate_hz: float, channel_count: int
) -> tuple[float, ...]:
    """Return deterministic EEG-like channel values in microvolts."""
    time_seconds = sample_index / sampling_rate_hz
    values: list[float] = []
    for channel in range(channel_count):
        alpha = 15.0 * math.sin(2 * math.pi * (8.0 + channel * 0.35) * time_seconds)
        beta = 5.0 * math.sin(2 * math.pi * (18.0 + channel * 0.2) * time_seconds)
        line = 1.5 * math.sin(2 * math.pi * 50.0 * time_seconds)
        noise_seed = (sample_index * 1103515245 + channel * 12345 + 1013904223) & 0xFFFF
        noise = (noise_seed / 65535.0 - 0.5) * 4.0
        values.append(alpha + beta + line + noise)
    return tuple(values)


class SyntheticAcquisitionBackend:
    def __init__(self, profile: DeviceProfile, clock: ProtocolClock) -> None:
        self.profile = profile
        self.clock = clock
        self._channel_names = tuple(channel.label for channel in profile.eeg_channels) + (
            "marker",
        )
        self._lock = threading.Lock()
        self._prepared = False
        self._running = False
        self._start_time = 0.0
        self._sample_cursor = 0
        self._markers: list[tuple[float, int]] = []

    @property
    def channel_names(self) -> tuple[str, ...]:
        return self._channel_names

    @property
    def supports_embedded_markers(self) -> bool:
        return True

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "implementation": "deterministic_in_process",
            "units": {channel.label: "uV" for channel in self.profile.eeg_channels},
            "marker_channel": len(self._channel_names) - 1,
            "eeg_channel_indexes": list(range(len(self.profile.eeg_channels))),
            "randomness": "closed-form deterministic signal",
        }

    def prepare(self) -> None:
        self._prepared = True

    def start(self) -> None:
        if not self._prepared:
            raise AcquisitionError("synthetic backend must be prepared before start")
        self._start_time = self.clock.monotonic()
        self._running = True

    def read_available(self) -> SampleBatch | None:
        with self._lock:
            if not self._running:
                return None
            elapsed = max(0.0, self.clock.monotonic() - self._start_time)
            target = int(elapsed * self.profile.sampling_rate_hz + 1e-9)
            if target <= self._sample_cursor:
                return None

            timestamps: list[float] = []
            samples: list[tuple[float, ...]] = []
            channel_count = len(self.profile.eeg_channels)
            for sample_index in range(self._sample_cursor, target):
                timestamp = self._start_time + sample_index / self.profile.sampling_rate_hz
                marker_value = 0.0
                if self._markers and self._markers[0][0] <= timestamp + 1e-9:
                    _, marker = self._markers.pop(0)
                    marker_value = float(marker)
                samples.append(
                    synthetic_eeg_sample(
                        sample_index, self.profile.sampling_rate_hz, channel_count
                    )
                    + (marker_value,)
                )
                timestamps.append(timestamp)
            self._sample_cursor = target
            values = tuple(timestamps)
            return SampleBatch(values, values, tuple(samples))

    def insert_marker(self, value: int, timestamp: float) -> bool:
        with self._lock:
            self._markers.append((timestamp, value))
            self._markers.sort(key=lambda marker: marker[0])
        return True

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._prepared = False
