from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ssvep_bci.acquisition.base import (
    AcquisitionDescriptor,
    AcquisitionError,
    MarkerReceipt,
    MarkerRequest,
    SampleBatch,
)
from ssvep_bci.config.models import BrainFlowConnection, DeviceProfile
from ssvep_bci.runtime.clock import Clock


class BrainFlowBackend:
    def __init__(self, profile: DeviceProfile, profile_path: Path, clock: Clock) -> None:
        self.profile = profile
        self.profile_path = profile_path
        self.clock = clock
        if not isinstance(profile.connection, BrainFlowConnection):
            raise ValueError("BrainFlow backend requires BrainFlowConnection")
        self.connection = profile.connection
        self._board = None
        self._board_shim = None
        self._timestamp_row: int | None = None
        self._marker_row: int | None = None
        self._master_board_id: int | None = None
        self._wall_to_monotonic = 0.0

    def prepare(self) -> AcquisitionDescriptor:
        try:
            from brainflow.board_shim import BoardShim, BrainFlowInputParams
        except ImportError as exc:
            raise AcquisitionError("BrainFlow is not installed") from exc
        params = BrainFlowInputParams()
        for field in (
            "mac_address", "ip_address", "ip_port", "ip_protocol", "other_info",
            "serial_number", "timeout",
        ):
            setattr(params, field, getattr(self.connection, field))
        if self.connection.serial_port == "auto":
            params.serial_port = self._find_unique_ftdi_port()
        elif self.connection.serial_port:
            params.serial_port = self.connection.serial_port
        if self.connection.file is not None:
            path = self.connection.file
            if not path.is_absolute():
                path = self.profile_path.parent / path
            params.file = str(path.resolve())
        try:
            self._board = BoardShim(self.connection.board_id, params)
            self._board.prepare_session()
            self._board_shim = BoardShim
            self._master_board_id = (
                self.connection.master_board_id
                if self.connection.master_board_id is not None
                else self._board.get_board_id()
            )
            actual_rate = float(self._board.get_board_sampling_rate())
            if abs(actual_rate - self.profile.sampling_rate_hz) > 1e-6:
                raise AcquisitionError(
                    f"BrainFlow sampling rate {actual_rate:g} does not match profile "
                    f"{self.profile.sampling_rate_hz:g}"
                )
            eeg_rows = set(BoardShim.get_eeg_channels(self._master_board_id))
            invalid = [c.source_index for c in self.profile.channels if c.source_index not in eeg_rows]
            if invalid:
                raise AcquisitionError(f"configured BrainFlow rows are not EEG rows: {invalid}")
            self._timestamp_row = BoardShim.get_timestamp_channel(self._master_board_id)
            self._marker_row = BoardShim.get_marker_channel(self._master_board_id)
            package_row = BoardShim.get_package_num_channel(self._master_board_id)
            self._wall_to_monotonic = self.clock.monotonic() - time.time()
            return AcquisitionDescriptor(
                backend="brainflow",
                profile_id=self.profile.profile_id,
                sampling_rate_hz=actual_rate,
                channels=self.profile.channels,
                supports_embedded_markers=True,
                metadata={
                    "configured_board_id": self.connection.board_id,
                    "master_board_id": self._master_board_id,
                    "timestamp_row": self._timestamp_row,
                    "marker_row": self._marker_row,
                    "sequence_row": package_row,
                    "timestamp_domain": "unix_epoch_seconds",
                    "wall_to_monotonic_offset": self._wall_to_monotonic,
                },
            )
        except Exception as exc:
            self.close()
            if isinstance(exc, AcquisitionError):
                raise
            raise AcquisitionError(f"BrainFlow prepare failed: {exc}") from exc

    def start(self) -> None:
        if self._board is None:
            raise AcquisitionError("BrainFlow backend is not prepared")
        try:
            self._board.start_stream(
                self.connection.ring_buffer_samples, self.connection.streamer_params
            )
        except Exception as exc:
            raise AcquisitionError(f"BrainFlow start failed: {exc}") from exc

    def read_available(self, max_samples: int) -> SampleBatch | None:
        if self._board is None or self._timestamp_row is None or self._marker_row is None:
            return None
        try:
            data = self._board.get_board_data(max_samples)
        except Exception as exc:
            raise AcquisitionError(f"BrainFlow read failed: {exc}") from exc
        if data.size == 0 or data.shape[1] == 0:
            return None
        receipt_mono = self.clock.monotonic()
        source = np.asarray(data[self._timestamp_row, :], dtype=np.float64)
        eeg = np.asarray(
            data[[channel.source_index for channel in self.profile.channels], :].T,
            dtype=np.float64,
        )
        markers = np.asarray(data[self._marker_row, :], dtype=np.float64)
        return SampleBatch(
            eeg=eeg,
            source_timestamps=source,
            corrected_source_timestamps=source.copy(),
            aligned_monotonic_timestamps=source + self._wall_to_monotonic,
            embedded_markers=markers,
            receipt_monotonic_timestamp=receipt_mono,
            receipt_wall_clock_timestamp_utc=self.clock.wall_time_utc(),
        )

    def insert_marker(self, request: MarkerRequest) -> MarkerReceipt:
        attempt = self.clock.monotonic()
        if self._board is None:
            return MarkerReceipt(request, True, False, attempt, "board is not prepared")
        try:
            self._board.insert_marker(float(request.marker_code))
            return MarkerReceipt(request, True, True, attempt)
        except Exception as exc:
            return MarkerReceipt(request, True, False, attempt, str(exc))

    def stop(self) -> None:
        if self._board is not None:
            try:
                self._board.stop_stream()
            except Exception as exc:
                raise AcquisitionError(f"BrainFlow stop failed: {exc}") from exc

    def close(self) -> None:
        if self._board is not None:
            try:
                self._board.release_session()
            except Exception:
                pass
        self._board = None

    @staticmethod
    def _find_unique_ftdi_port() -> str:
        try:
            from serial.tools import list_ports
        except ImportError as exc:
            raise AcquisitionError("pyserial is required for serial_port: auto") from exc
        matches = [
            port.device for port in list_ports.comports()
            if "FTDI" in (port.manufacturer or "").upper()
            or "OPENBCI" in (port.description or "").upper()
        ]
        if len(matches) != 1:
            raise AcquisitionError(
                f"serial_port: auto requires exactly one FTDI/OpenBCI port; found {matches}"
            )
        return matches[0]

