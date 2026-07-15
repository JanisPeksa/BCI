"""Lazy BrainFlow adapter for Cyton and playback-file profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from imagined_speech.acquisition.base import AcquisitionError, SampleBatch
from imagined_speech.config import DeviceProfile


class BrainFlowAcquisitionBackend:
    def __init__(self, profile: DeviceProfile, profile_path: Path) -> None:
        self.profile = profile
        self.profile_path = profile_path
        self._board: Any = None
        self._board_shim_type: Any = None
        self._channel_names: tuple[str, ...] = ()
        self._timestamp_channel: int | None = None
        self._master_board_id: int | None = None
        self._metadata: dict[str, Any] = {}

    @property
    def channel_names(self) -> tuple[str, ...]:
        return self._channel_names

    @property
    def supports_embedded_markers(self) -> bool:
        return True

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def prepare(self) -> None:
        try:
            from brainflow.board_shim import BoardShim, BrainFlowInputParams
        except ImportError as exc:
            raise AcquisitionError(
                "BrainFlow is not installed; install the 'acquisition' extra"
            ) from exc
        if self.profile.board_id is None:
            raise AcquisitionError("BrainFlow profile has no board ID")

        params = BrainFlowInputParams()
        connection = dict(self.profile.connection)
        for field in (
            "serial_port",
            "mac_address",
            "ip_address",
            "ip_port",
            "ip_protocol",
            "other_info",
            "serial_number",
            "timeout",
            "master_board",
        ):
            value = connection.get(field)
            if value is not None:
                setattr(params, field, value)
        file_value = connection.get("file")
        if self.profile.backend == "replay":
            if not file_value:
                raise AcquisitionError(
                    "replay device profile requires connection.file to reference a BrainFlow recording"
                )
            replay_path = Path(str(file_value)).expanduser()
            if not replay_path.is_absolute():
                replay_path = self.profile_path.parent / replay_path
            params.file = str(replay_path.resolve())

        try:
            self._board = BoardShim(self.profile.board_id, params)
            self._board.prepare_session()
            self._board_shim_type = BoardShim
            self._master_board_id = int(
                connection.get("master_board")
                if connection.get("master_board") is not None
                else self.profile.board_id
            )
            row_count = BoardShim.get_num_rows(self._master_board_id)
            self._timestamp_channel = BoardShim.get_timestamp_channel(
                self._master_board_id
            )
            marker_channel = BoardShim.get_marker_channel(self._master_board_id)
            package_num_channel = BoardShim.get_package_num_channel(
                self._master_board_id
            )
            channel_names = [f"board_row_{index:03d}" for index in range(row_count)]
            channel_names[package_num_channel] = "package_number"
            channel_names[self._timestamp_channel] = "board_timestamp"
            channel_names[marker_channel] = "board_marker"
            for channel in self.profile.eeg_channels:
                if channel.board_channel >= row_count:
                    raise AcquisitionError(
                        f"configured EEG row {channel.board_channel} exceeds BrainFlow row count {row_count}"
                    )
                channel_names[channel.board_channel] = channel.label
            self._channel_names = tuple(channel_names)
            self._metadata = {
                "implementation": "brainflow",
                "configured_board_id": self.profile.board_id,
                "master_board_id": self._master_board_id,
                "brainflow_sampling_rate_hz": BoardShim.get_sampling_rate(
                    self._master_board_id
                ),
                "timestamp_channel": self._timestamp_channel,
                "marker_channel": marker_channel,
                "sequence_channel": package_num_channel,
                "sequence_modulus": 256,
                "eeg_channel_indexes": [
                    channel.board_channel for channel in self.profile.eeg_channels
                ],
            }
        except Exception as exc:
            self.close()
            if isinstance(exc, AcquisitionError):
                raise
            raise AcquisitionError(f"BrainFlow prepare failed: {exc}") from exc

    def start(self) -> None:
        if self._board is None:
            raise AcquisitionError("BrainFlow backend must be prepared before start")
        try:
            self._board.start_stream(450000)
        except Exception as exc:
            raise AcquisitionError(f"BrainFlow stream start failed: {exc}") from exc

    def read_available(self) -> SampleBatch | None:
        if self._board is None or self._timestamp_channel is None:
            return None
        try:
            data = self._board.get_board_data()
        except Exception as exc:
            raise AcquisitionError(f"BrainFlow read failed: {exc}") from exc
        if data.size == 0 or data.shape[1] == 0:
            return None
        timestamps = tuple(float(value) for value in data[self._timestamp_channel, :])
        samples = tuple(tuple(float(value) for value in row) for row in data.T)
        return SampleBatch(timestamps, timestamps, samples)

    def insert_marker(self, value: int, timestamp: float) -> bool:
        del timestamp
        if self._board is None:
            return False
        try:
            self._board.insert_marker(float(value))
        except Exception as exc:
            raise AcquisitionError(f"BrainFlow marker insertion failed: {exc}") from exc
        return True

    def stop(self) -> None:
        if self._board is not None:
            try:
                self._board.stop_stream()
            except Exception as exc:
                raise AcquisitionError(f"BrainFlow stream stop failed: {exc}") from exc

    def close(self) -> None:
        if self._board is not None:
            try:
                self._board.release_session()
            except Exception:
                pass
            self._board = None
