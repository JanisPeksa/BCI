from __future__ import annotations

import csv
import queue
import threading
import time
from pathlib import Path

from psychopy_ssvep.acquisition.base import SampleBatch
from psychopy_ssvep.config.models import ChannelConfig


_STOP = object()


class RawCsvWriter:
    def __init__(
        self,
        path: Path,
        channels: tuple[ChannelConfig, ...],
        flush_every_batch: bool = True,
    ) -> None:
        self.path = path
        self.channels = channels
        self.flush_every_batch = flush_every_batch
        self._queue: queue.Queue[SampleBatch | object] = queue.Queue(maxsize=128)
        self._thread: threading.Thread | None = None
        self.sample_count = 0
        self.error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="eeg-raw-writer", daemon=True)
        self._thread.start()

    def submit(self, batch: SampleBatch) -> None:
        if self.error is not None:
            raise RuntimeError(f"raw writer failed: {self.error}")
        try:
            self._queue.put_nowait(batch)
        except queue.Full as exc:
            raise RuntimeError("raw EEG writer queue overrun") from exc

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        deadline = time.monotonic() + 30.0
        while thread.is_alive():
            try:
                self._queue.put(_STOP, timeout=0.1)
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise RuntimeError("raw EEG writer queue did not drain during shutdown")
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            raise RuntimeError("raw EEG writer did not stop")
        if self.error is not None:
            raise RuntimeError(f"raw EEG writer failed: {self.error}")

    def _run(self) -> None:
        try:
            with self.path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow([
                    "sample_index",
                    "receipt_monotonic_timestamp",
                    "receipt_wall_clock_timestamp_utc",
                    "source_timestamp",
                    "corrected_source_timestamp",
                    "aligned_monotonic_timestamp",
                    *(channel.label for channel in self.channels),
                    "embedded_marker",
                ])
                handle.flush()
                while True:
                    item = self._queue.get()
                    try:
                        if item is _STOP:
                            return
                        assert isinstance(item, SampleBatch)
                        for row in range(item.sample_count):
                            marker = "" if item.embedded_markers is None else f"{item.embedded_markers[row]:.9f}"
                            writer.writerow([
                                self.sample_count,
                                f"{item.receipt_monotonic_timestamp:.9f}",
                                item.receipt_wall_clock_timestamp_utc.isoformat(),
                                f"{item.source_timestamps[row]:.9f}",
                                f"{item.corrected_source_timestamps[row]:.9f}",
                                f"{item.aligned_monotonic_timestamps[row]:.9f}",
                                *(f"{value:.9f}" for value in item.eeg[row]),
                                marker,
                            ])
                            self.sample_count += 1
                        if self.flush_every_batch:
                            handle.flush()
                    finally:
                        self._queue.task_done()
        except Exception as exc:
            self.error = exc
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
