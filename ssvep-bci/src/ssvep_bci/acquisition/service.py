from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable

from ssvep_bci.acquisition.base import (
    AcquisitionBackend,
    AcquisitionDescriptor,
    MarkerReceipt,
    MarkerRequest,
    SampleBatch,
)
from ssvep_bci.acquisition.brainflow import BrainFlowBackend
from ssvep_bci.acquisition.lsl import LslBackend
from ssvep_bci.acquisition.synthetic import SyntheticBackend
from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.events.models import EventType, ProtocolEvent
from ssvep_bci.runtime.clock import Clock


def create_backend(resolved: ResolvedExperiment, clock: Clock) -> AcquisitionBackend:
    if resolved.device.backend == "synthetic":
        return SyntheticBackend(resolved.device, clock)
    if resolved.device.backend == "brainflow":
        return BrainFlowBackend(resolved.device, resolved.device_path, clock)
    if resolved.device.backend == "lsl":
        return LslBackend(resolved.device, clock)
    raise ValueError(f"unsupported backend: {resolved.device.backend}")


class AcquisitionService:
    """Owns one backend and serializes every backend call on one thread."""

    def __init__(
        self,
        resolved: ResolvedExperiment,
        clock: Clock,
        on_batch: Callable[[SampleBatch], None],
        on_marker: Callable[[MarkerReceipt], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.resolved = resolved
        self.clock = clock
        self.backend = create_backend(resolved, clock)
        self.on_batch = on_batch
        self.on_marker = on_marker
        self.on_error = on_error
        self.descriptor: AcquisitionDescriptor | None = None
        self._markers: queue.Queue[MarkerRequest] = queue.Queue(maxsize=1024)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        connection = resolved.device.connection
        self._poll_interval = connection.poll_interval_seconds
        self._chunk_samples = connection.read_chunk_samples

    def start(self) -> AcquisitionDescriptor:
        try:
            self.descriptor = self.backend.prepare()
            self.backend.start()
        except Exception:
            self.backend.close()
            raise
        self._thread = threading.Thread(
            target=self._loop, name="eeg-acquisition", daemon=True
        )
        self._thread.start()
        return self.descriptor

    def emit(self, event: ProtocolEvent) -> None:
        if event.marker_code is None:
            return
        frequency = None
        if event.event_type == EventType.STIMULUS_ONSET and event.stimulus_id:
            frequency = next(
                stimulus.frequency_hz
                for stimulus in self.resolved.config.stimuli
                if stimulus.id == event.stimulus_id
            )
        request = MarkerRequest(
            event_id=event.event_id,
            event_sequence=event.sequence_number,
            event_type=event.event_type.value,
            marker_code=event.marker_code,
            event_monotonic_timestamp=event.monotonic_timestamp,
            event_wall_clock_timestamp_utc=event.wall_clock_timestamp_utc,
            stimulus_frequency_hz=frequency,
        )
        try:
            self._markers.put_nowait(request)
        except queue.Full as exc:
            self.on_error(exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                try:
                    self.backend.stop()
                finally:
                    self.backend.close()
                self._thread.join(timeout=1)
                raise RuntimeError("acquisition worker did not stop cleanly")
        errors: list[str] = []
        try:
            try:
                batch = self.backend.read_available(self._chunk_samples)
                if batch is not None:
                    self.on_batch(batch)
            except Exception as exc:
                errors.append(f"final acquisition read failed: {exc}")
            try:
                self.backend.stop()
            except Exception as exc:
                errors.append(f"acquisition stop failed: {exc}")
            try:
                batch = self.backend.read_available(self._chunk_samples)
                if batch is not None:
                    self.on_batch(batch)
            except Exception as exc:
                errors.append(f"post-stop acquisition drain failed: {exc}")
        finally:
            try:
                self.backend.close()
            except Exception as exc:
                errors.append(f"acquisition close failed: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _loop(self) -> None:
        consecutive_errors = 0
        while not self._stop.is_set():
            try:
                self._drain_markers()
                batch = self.backend.read_available(self._chunk_samples)
                if batch is not None:
                    self.on_batch(batch)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self.on_error(exc)
                    self._stop.set()
                    break
            self._stop.wait(self._poll_interval)
        try:
            self._drain_markers()
        except Exception as exc:
            self.on_error(exc)

    def _drain_markers(self) -> None:
        while True:
            try:
                request = self._markers.get_nowait()
            except queue.Empty:
                return
            try:
                self.on_marker(self.backend.insert_marker(request))
            finally:
                self._markers.task_done()
