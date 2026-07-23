from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np

from ssvep_bci.acquisition.base import (
    AcquisitionDescriptor,
    AcquisitionError,
    MarkerReceipt,
    MarkerRequest,
    SampleBatch,
)
from ssvep_bci.config.models import DeviceProfile, SyntheticConnection
from ssvep_bci.runtime.clock import Clock


class SyntheticBackend:
    def __init__(self, profile: DeviceProfile, clock: Clock) -> None:
        self.profile = profile
        self.clock = clock
        if not isinstance(profile.connection, SyntheticConnection):
            raise ValueError("synthetic backend requires SyntheticConnection")
        self.connection = profile.connection
        self._prepared = False
        self._running = False
        self._start = 0.0
        self._cursor = 0
        self._markers: list[tuple[float, int]] = []
        self._stimulus_transitions: list[tuple[float, float | None]] = []

    def prepare(self) -> AcquisitionDescriptor:
        self._prepared = True
        return AcquisitionDescriptor(
            backend="synthetic",
            profile_id=self.profile.profile_id,
            sampling_rate_hz=self.profile.sampling_rate_hz,
            channels=self.profile.channels,
            supports_embedded_markers=True,
            metadata={
                "implementation": "deterministic_closed_form",
                "seed": self.connection.seed,
                "timestamp_domain": "application_monotonic",
            },
        )

    def start(self) -> None:
        if not self._prepared:
            raise AcquisitionError("synthetic backend is not prepared")
        self._start = self.clock.monotonic()
        self._running = True

    def read_available(self, max_samples: int) -> SampleBatch | None:
        if not self._running:
            return None
        receipt_mono = self.clock.monotonic()
        target = int(max(0.0, receipt_mono - self._start) * self.profile.sampling_rate_hz + 1e-9)
        target = min(target, self._cursor + max_samples)
        if target <= self._cursor:
            return None
        indexes = np.arange(self._cursor, target, dtype=np.int64)
        timestamps = self._start + indexes.astype(np.float64) / self.profile.sampling_rate_hz
        eeg = np.empty((len(indexes), len(self.profile.channels)), dtype=np.float64)
        markers = np.zeros(len(indexes), dtype=np.float64)
        for row, (sample_index, timestamp) in enumerate(zip(indexes, timestamps, strict=True)):
            active_frequency = self._frequency_at(float(timestamp))
            for channel in range(len(self.profile.channels)):
                t = sample_index / self.profile.sampling_rate_hz
                baseline = self.connection.baseline_amplitude_uv * math.sin(
                    2 * math.pi * (8.0 + channel * 0.2) * t
                )
                response = 0.0
                if active_frequency is not None:
                    response = self.connection.ssvep_amplitude_uv * (
                        math.sin(2 * math.pi * active_frequency * t)
                        + 0.35 * math.sin(2 * math.pi * 2 * active_frequency * t)
                    )
                seed = (
                    int(sample_index) * 1103515245
                    + channel * 12345
                    + self.connection.seed
                ) & 0xFFFF
                noise = (seed / 65535.0 - 0.5) * 2 * self.connection.noise_amplitude_uv
                eeg[row, channel] = baseline + response + noise
            for marker_index, (marker_time, marker_code) in enumerate(self._markers):
                if marker_time <= timestamp + 1e-9:
                    markers[row] = marker_code
                    del self._markers[marker_index]
                    break
        self._cursor = target
        return SampleBatch(
            eeg=eeg,
            source_timestamps=timestamps.copy(),
            corrected_source_timestamps=timestamps.copy(),
            aligned_monotonic_timestamps=timestamps.copy(),
            embedded_markers=markers,
            receipt_monotonic_timestamp=receipt_mono,
            receipt_wall_clock_timestamp_utc=self.clock.wall_time_utc(),
        )

    def insert_marker(self, request: MarkerRequest) -> MarkerReceipt:
        attempt = self.clock.monotonic()
        self._markers.append((request.event_monotonic_timestamp, request.marker_code))
        self._markers.sort()
        if request.event_type == "stimulus_onset":
            self._stimulus_transitions.append(
                (request.event_monotonic_timestamp, request.stimulus_frequency_hz)
            )
        elif request.event_type == "stimulus_offset":
            self._stimulus_transitions.append((request.event_monotonic_timestamp, None))
        self._stimulus_transitions.sort(key=lambda item: item[0])
        return MarkerReceipt(request, True, True, attempt)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self._prepared = False

    def _frequency_at(self, timestamp: float) -> float | None:
        active = None
        for transition_time, frequency in self._stimulus_transitions:
            if transition_time > timestamp + 1e-9:
                break
            active = frequency
        return active

