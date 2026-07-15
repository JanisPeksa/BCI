"""Lab Streaming Layer EEG inlet backend."""

from __future__ import annotations

from typing import Any

from imagined_speech.acquisition.base import AcquisitionError, SampleBatch
from imagined_speech.config import DeviceProfile


class LSLAcquisitionBackend:
    def __init__(self, profile: DeviceProfile) -> None:
        self.profile = profile
        self._inlet: Any = None
        self._channel_names = tuple(channel.label for channel in profile.eeg_channels)
        self._time_correction = 0.0
        self._metadata: dict[str, Any] = {}

    @property
    def channel_names(self) -> tuple[str, ...]:
        return self._channel_names

    @property
    def supports_embedded_markers(self) -> bool:
        return False

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def prepare(self) -> None:
        try:
            import pylsl
        except ImportError as exc:
            raise AcquisitionError(
                "pylsl is not installed; install the 'acquisition' extra"
            ) from exc

        connection = self.profile.connection
        stream_name = connection.get("stream_name")
        stream_type = connection.get("stream_type")
        timeout = float(connection.get("resolve_timeout_seconds") or 5.0)
        property_name = "name" if stream_name else "type"
        property_value = str(stream_name or stream_type)
        try:
            streams = pylsl.resolve_byprop(
                property_name, property_value, minimum=1, timeout=timeout
            )
            if stream_type:
                streams = [stream for stream in streams if stream.type() == stream_type]
            if not streams:
                raise AcquisitionError(
                    f"no LSL stream matched {property_name}={property_value!r} within {timeout:g}s"
                )
            info = streams[0]
            if info.channel_count() != len(self.profile.eeg_channels):
                raise AcquisitionError(
                    f"LSL stream has {info.channel_count()} channels; profile expects "
                    f"{len(self.profile.eeg_channels)}"
                )
            self._inlet = pylsl.StreamInlet(
                info, max_buflen=360, max_chunklen=1024, recover=True
            )
            self._inlet.open_stream(timeout=timeout)
            try:
                self._time_correction = float(self._inlet.time_correction(timeout=timeout))
            except Exception:
                self._time_correction = 0.0
            self._metadata = {
                "implementation": "lsl",
                "stream_name": info.name(),
                "stream_type": info.type(),
                "source_id": info.source_id(),
                "uid": info.uid(),
                "hostname": info.hostname(),
                "nominal_sampling_rate_hz": info.nominal_srate(),
                "time_correction_seconds": self._time_correction,
                "eeg_channel_indexes": list(range(info.channel_count())),
            }
        except AcquisitionError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise AcquisitionError(f"LSL stream preparation failed: {exc}") from exc

    def start(self) -> None:
        if self._inlet is None:
            raise AcquisitionError("LSL backend must be prepared before start")

    def read_available(self) -> SampleBatch | None:
        if self._inlet is None:
            return None
        try:
            samples, timestamps = self._inlet.pull_chunk(timeout=0.0, max_samples=1024)
        except Exception as exc:
            raise AcquisitionError(f"LSL read failed: {exc}") from exc
        if not timestamps:
            return None
        source = tuple(float(timestamp) for timestamp in timestamps)
        corrected = tuple(timestamp + self._time_correction for timestamp in source)
        rows = tuple(tuple(float(value) for value in sample) for sample in samples)
        return SampleBatch(source, corrected, rows)

    def insert_marker(self, value: int, timestamp: float) -> bool:
        del value, timestamp
        return False

    def stop(self) -> None:
        return None

    def close(self) -> None:
        if self._inlet is not None:
            try:
                self._inlet.close_stream()
            except Exception:
                pass
            self._inlet = None
