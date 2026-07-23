from __future__ import annotations

import numpy as np

from ssvep_bci.acquisition.base import (
    AcquisitionDescriptor,
    AcquisitionError,
    MarkerReceipt,
    MarkerRequest,
    SampleBatch,
)
from ssvep_bci.config.models import DeviceProfile, LslConnection
from ssvep_bci.runtime.clock import Clock


class LslBackend:
    def __init__(self, profile: DeviceProfile, clock: Clock) -> None:
        self.profile = profile
        self.clock = clock
        if not isinstance(profile.connection, LslConnection):
            raise ValueError("LSL backend requires LslConnection")
        self.connection = profile.connection
        self._pylsl = None
        self._inlet = None
        self._correction = 0.0
        self._lsl_to_monotonic = 0.0
        self._next_correction_refresh = 0.0

    def prepare(self) -> AcquisitionDescriptor:
        try:
            import pylsl
        except ImportError as exc:
            raise AcquisitionError("pylsl is not installed") from exc
        self._pylsl = pylsl
        selector_name, selector_value = self._primary_selector()
        streams = pylsl.resolve_byprop(
            selector_name,
            selector_value,
            minimum=1,
            timeout=self.connection.resolve_timeout_seconds,
        )
        streams = [stream for stream in streams if self._matches(stream)]
        if len(streams) != 1:
            raise AcquisitionError(
                f"LSL profile must resolve exactly one stream; found {len(streams)}"
            )
        info = streams[0]
        width = int(info.channel_count())
        highest = max(channel.source_index for channel in self.profile.channels)
        if highest >= width:
            raise AcquisitionError(
                f"LSL stream has {width} channels but profile requests index {highest}"
            )
        nominal_rate = float(info.nominal_srate())
        if nominal_rate <= 0 or abs(nominal_rate - self.profile.sampling_rate_hz) > 1e-6:
            raise AcquisitionError(
                f"LSL nominal rate {nominal_rate:g} does not match profile "
                f"{self.profile.sampling_rate_hz:g}"
            )
        self._inlet = pylsl.StreamInlet(info, max_buflen=360, max_chunklen=1024, recover=True)
        self._inlet.open_stream(timeout=self.connection.resolve_timeout_seconds)
        self._correction = float(
            self._inlet.time_correction(timeout=self.connection.resolve_timeout_seconds)
        )
        self._lsl_to_monotonic = self.clock.monotonic() - pylsl.local_clock()
        self._next_correction_refresh = (
            self.clock.monotonic() + self.connection.correction_refresh_seconds
        )
        return AcquisitionDescriptor(
            backend="lsl",
            profile_id=self.profile.profile_id,
            sampling_rate_hz=nominal_rate,
            channels=self.profile.channels,
            supports_embedded_markers=False,
            metadata={
                "stream_name": info.name(),
                "stream_type": info.type(),
                "source_id": info.source_id(),
                "uid": info.uid(),
                "time_correction_seconds": self._correction,
                "lsl_to_monotonic_offset": self._lsl_to_monotonic,
                "timestamp_domain": "lsl_local_clock",
            },
        )

    def start(self) -> None:
        if self._inlet is None:
            raise AcquisitionError("LSL backend is not prepared")

    def read_available(self, max_samples: int) -> SampleBatch | None:
        if self._inlet is None:
            return None
        try:
            now = self.clock.monotonic()
            if now >= self._next_correction_refresh:
                self._correction = float(self._inlet.time_correction(
                    timeout=self.connection.recovery_timeout_seconds
                ))
                self._next_correction_refresh = (
                    now + self.connection.correction_refresh_seconds
                )
            samples, timestamps = self._inlet.pull_chunk(timeout=0.0, max_samples=max_samples)
        except Exception as exc:
            raise AcquisitionError(f"LSL read failed: {exc}") from exc
        if not timestamps:
            return None
        raw = np.asarray(samples, dtype=np.float64)
        eeg = raw[:, [channel.source_index for channel in self.profile.channels]]
        source = np.asarray(timestamps, dtype=np.float64)
        corrected = source + self._correction
        return SampleBatch(
            eeg=eeg,
            source_timestamps=source,
            corrected_source_timestamps=corrected,
            aligned_monotonic_timestamps=corrected + self._lsl_to_monotonic,
            embedded_markers=None,
            receipt_monotonic_timestamp=self.clock.monotonic(),
            receipt_wall_clock_timestamp_utc=self.clock.wall_time_utc(),
        )

    def insert_marker(self, request: MarkerRequest) -> MarkerReceipt:
        return MarkerReceipt(request, False, False, self.clock.monotonic())

    def stop(self) -> None:
        return None

    def close(self) -> None:
        if self._inlet is not None:
            try:
                self._inlet.close_stream()
            except Exception:
                pass
        self._inlet = None

    def _primary_selector(self) -> tuple[str, str]:
        if self.connection.stream_name:
            return "name", self.connection.stream_name
        if self.connection.source_id:
            return "source_id", self.connection.source_id
        assert self.connection.stream_type
        return "type", self.connection.stream_type

    def _matches(self, info) -> bool:
        return all((
            not self.connection.stream_name or info.name() == self.connection.stream_name,
            not self.connection.stream_type or info.type() == self.connection.stream_type,
            not self.connection.source_id or info.source_id() == self.connection.source_id,
        ))
