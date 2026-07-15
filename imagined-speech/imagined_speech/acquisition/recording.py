"""Continuous acquisition worker, raw writer, markers, and health metadata."""

from __future__ import annotations

import csv
import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from imagined_speech.acquisition.base import AcquisitionBackend, AcquisitionError, SampleBatch
from imagined_speech.engine import ProtocolClock, VirtualClock
from imagined_speech.events import EventSink, ProtocolEvent


@dataclass(frozen=True)
class _QueuedBatch:
    batch: SampleBatch
    receipt_monotonic: float
    receipt_wall_utc: datetime


_QUEUE_STOP = object()


@dataclass(frozen=True)
class AcquisitionSnapshot:
    running: bool
    sample_count: int
    dropped_batches: int
    dropped_samples: int
    timestamp_discontinuities: int
    read_errors: int
    write_errors: int
    last_health_kind: str
    last_health_severity: str
    channel_names: tuple[str, ...]
    recent_samples: tuple[tuple[float, ...], ...]


class AcquisitionRecorder(EventSink):
    """Records raw source rows independently of protocol and UI execution."""

    artifact_names = (
        "eeg_raw.csv",
        "acquisition-markers.jsonl",
        "acquisition-health.jsonl",
        "acquisition-metadata.json",
    )

    def __init__(
        self,
        session_path: Path,
        backend: AcquisitionBackend,
        clock: ProtocolClock,
        *,
        queue_capacity: int = 64,
        poll_interval_seconds: float = 0.02,
    ) -> None:
        self.session_path = session_path
        self.backend = backend
        self.clock = clock
        self.poll_interval_seconds = poll_interval_seconds
        self._queue: queue.Queue[_QueuedBatch | object] = queue.Queue(queue_capacity)
        self._stop_reader = threading.Event()
        self._read_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._raw_file: Any = None
        self._raw_writer: csv.writer | None = None
        self._marker_file: Any = None
        self._health_file: Any = None
        self._running = False
        self._closed = False
        self._sample_count = 0
        self._dropped_batches = 0
        self._dropped_samples = 0
        self._timestamp_gaps = 0
        self._read_errors = 0
        self._write_errors = 0
        self._first_source_timestamp: float | None = None
        self._last_source_timestamp: float | None = None
        self._last_source_sequence: int | None = None
        self._started_monotonic: float | None = None
        self._stopped_monotonic: float | None = None
        self._started_wall: datetime | None = None
        self._stopped_wall: datetime | None = None
        recent_capacity = max(250, int(5 * backend.profile.sampling_rate_hz))
        self._recent_samples: deque[tuple[float, ...]] = deque(maxlen=recent_capacity)
        self._last_health_kind = "created"
        self._last_health_severity = "info"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def dropped_samples(self) -> int:
        return self._dropped_samples

    def snapshot(self, max_samples: int = 750) -> AcquisitionSnapshot:
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")
        with self._snapshot_lock:
            recent = tuple(self._recent_samples)[-max_samples:]
            return AcquisitionSnapshot(
                running=self._running,
                sample_count=self._sample_count,
                dropped_batches=self._dropped_batches,
                dropped_samples=self._dropped_samples,
                timestamp_discontinuities=self._timestamp_gaps,
                read_errors=self._read_errors,
                write_errors=self._write_errors,
                last_health_kind=self._last_health_kind,
                last_health_severity=self._last_health_severity,
                channel_names=self.backend.channel_names,
                recent_samples=recent,
            )

    def flush_pending(self) -> None:
        """Wait until all batches already accepted by the writer queue are handled."""
        self._queue.join()

    def start(self) -> None:
        if self._running or self._closed:
            raise RuntimeError("acquisition recorder cannot be started in its current state")
        self._marker_file = (self.session_path / "acquisition-markers.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        )
        self._health_file = (self.session_path / "acquisition-health.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        )
        self._health("preparing", "info")
        try:
            self.backend.prepare()
            self._raw_file = (self.session_path / "eeg_raw.csv").open(
                "w", encoding="utf-8", newline=""
            )
            self._raw_writer = csv.writer(self._raw_file, lineterminator="\n")
            self._raw_writer.writerow(
                (
                    "sample_index",
                    "receipt_monotonic_seconds",
                    "receipt_time_utc",
                    "source_timestamp",
                    "corrected_source_timestamp",
                    *self.backend.channel_names,
                )
            )
            self._raw_file.flush()
            self.backend.start()
            self._started_monotonic = self.clock.monotonic()
            self._started_wall = self.clock.wall_time_utc()
            self._running = True
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name="eeg-raw-writer",
                daemon=True,
            )
            self._writer_thread.start()
            if not isinstance(self.clock, VirtualClock):
                self._reader_thread = threading.Thread(
                    target=self._reader_loop,
                    name="eeg-source-reader",
                    daemon=True,
                )
                self._reader_thread.start()
            self._health("started", "info")
        except Exception as exc:
            self._health("startup_failed", "error", error=str(exc))
            self._close_files()
            self.backend.close()
            self._write_metadata("failed", startup_error=str(exc))
            self._closed = True
            if isinstance(exc, AcquisitionError):
                raise
            raise AcquisitionError(f"acquisition startup failed: {exc}") from exc

    def capture_available(self) -> None:
        if self._running:
            self._read_and_enqueue(block=isinstance(self.clock, VirtualClock))

    def emit(self, event: ProtocolEvent) -> None:
        if not self._running:
            return
        embedded = False
        marker_error: str | None = None
        try:
            embedded = self.backend.insert_marker(
                event.marker_code, event.monotonic_seconds
            )
        except Exception as exc:
            marker_error = str(exc)
            self._health(
                "marker_insertion_failed",
                "warning",
                event_sequence=event.sequence_number,
                error=marker_error,
            )
        record = {
            "schema_version": 1,
            "event_sequence": event.sequence_number,
            "event_type": event.event_type.value,
            "marker_code": event.marker_code,
            "event_monotonic_seconds": event.monotonic_seconds,
            "event_wall_time_utc": event.wall_time_utc.isoformat(),
            "marker_request_monotonic_seconds": self.clock.monotonic(),
            "embedded_by_backend": embedded,
            "error": marker_error,
        }
        with self._log_lock:
            assert self._marker_file is not None
            self._marker_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._marker_file.flush()
        if isinstance(self.clock, VirtualClock):
            self.capture_available()

    def stop(self) -> None:
        if self._closed:
            return
        if not self._running:
            self._close_files()
            self._write_metadata("stopped")
            self._closed = True
            return

        self._stop_reader.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=5)
        stop_error: str | None = None
        try:
            self.backend.stop()
        except Exception as exc:
            stop_error = str(exc)
            self._health("stop_failed", "error", error=stop_error)
        try:
            self._read_and_enqueue(block=True)
        except Exception as exc:
            self._read_errors += 1
            self._health("final_read_failed", "error", error=str(exc))
        self._running = False
        self._queue.put(_QUEUE_STOP)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=30)
            if self._writer_thread.is_alive():
                self._health("writer_shutdown_timeout", "error")
        try:
            self.backend.close()
        except Exception as exc:
            self._health("backend_close_failed", "warning", error=str(exc))
        self._stopped_monotonic = self.clock.monotonic()
        self._stopped_wall = self.clock.wall_time_utc()
        self._health("stopped", "info", sample_count=self._sample_count)
        self._close_files()
        status = (
            "failed"
            if stop_error or self._read_errors or self._write_errors
            else "complete"
        )
        self._write_metadata(status, stop_error=stop_error)
        self._closed = True

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set():
            try:
                self._read_and_enqueue(block=False)
            except Exception as exc:
                self._read_errors += 1
                self._health("read_failed", "error", error=str(exc))
                self._stop_reader.set()
                return
            self._stop_reader.wait(self.poll_interval_seconds)

    def _read_and_enqueue(self, *, block: bool) -> None:
        with self._read_lock:
            batch = self.backend.read_available()
        if batch is None or batch.sample_count == 0:
            return
        queued = _QueuedBatch(
            batch=batch,
            receipt_monotonic=self.clock.monotonic(),
            receipt_wall_utc=self.clock.wall_time_utc(),
        )
        try:
            if block:
                self._queue.put(queued, timeout=30)
            else:
                self._queue.put_nowait(queued)
        except queue.Full:
            self._dropped_batches += 1
            self._dropped_samples += batch.sample_count
            self._health(
                "writer_queue_overrun",
                "error",
                dropped_samples=batch.sample_count,
            )

    def _writer_loop(self) -> None:
        while True:
            queued = self._queue.get()
            try:
                if queued is _QUEUE_STOP:
                    return
                assert isinstance(queued, _QueuedBatch)
                try:
                    self._write_batch(queued)
                except Exception as exc:
                    self._write_errors += 1
                    self._health("write_failed", "error", error=str(exc))
            finally:
                self._queue.task_done()

    def _write_batch(self, queued: _QueuedBatch) -> None:
        assert self._raw_writer is not None and self._raw_file is not None
        expected_interval = 1.0 / self.backend.profile.sampling_rate_hz
        backend_metadata = self.backend.metadata
        sequence_channel = backend_metadata.get("sequence_channel")
        sequence_modulus = int(backend_metadata.get("sequence_modulus", 0))
        for source_timestamp, corrected_timestamp, sample in zip(
            queued.batch.source_timestamps,
            queued.batch.corrected_timestamps,
            queued.batch.samples,
            strict=True,
        ):
            if len(sample) != len(self.backend.channel_names):
                raise AcquisitionError(
                    "acquisition sample width does not match the prepared channel catalog"
                )
            if sequence_channel is not None:
                sequence = int(round(sample[int(sequence_channel)]))
                if self._last_source_sequence is not None:
                    expected_sequence = (
                        self._last_source_sequence + 1
                    ) % sequence_modulus
                    if sequence != expected_sequence:
                        self._timestamp_gaps += 1
                        self._health(
                            "sample_sequence_gap",
                            "warning",
                            expected_sequence=expected_sequence,
                            sequence=sequence,
                        )
                self._last_source_sequence = sequence
            elif self._last_source_timestamp is not None:
                delta = corrected_timestamp - self._last_source_timestamp
                if delta <= 0 or delta > expected_interval * 2.5:
                    self._timestamp_gaps += 1
                    self._health(
                        "timestamp_discontinuity",
                        "warning",
                        previous_timestamp=self._last_source_timestamp,
                        timestamp=corrected_timestamp,
                        delta_seconds=delta,
                    )
            if self._first_source_timestamp is None:
                self._first_source_timestamp = corrected_timestamp
            self._last_source_timestamp = corrected_timestamp
            self._raw_writer.writerow(
                (
                    self._sample_count,
                    f"{queued.receipt_monotonic:.9f}",
                    queued.receipt_wall_utc.isoformat(),
                    f"{source_timestamp:.9f}",
                    f"{corrected_timestamp:.9f}",
                    *(f"{value:.9f}" for value in sample),
                )
            )
            with self._snapshot_lock:
                self._recent_samples.append(tuple(sample))
                self._sample_count += 1
        self._raw_file.flush()

    def _health(self, kind: str, severity: str, **details: Any) -> None:
        record = {
            "schema_version": 1,
            "kind": kind,
            "severity": severity,
            "monotonic_seconds": self.clock.monotonic(),
            "wall_time_utc": self.clock.wall_time_utc().isoformat(),
            **details,
        }
        with self._snapshot_lock:
            self._last_health_kind = kind
            self._last_health_severity = severity
        with self._log_lock:
            if self._health_file is not None and not self._health_file.closed:
                self._health_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._health_file.flush()

    def _close_files(self) -> None:
        for handle in (self._raw_file, self._marker_file, self._health_file):
            if handle is not None and not handle.closed:
                handle.flush()
                handle.close()

    def _write_metadata(self, status: str, **details: Any) -> None:
        effective_rate: float | None = None
        if (
            self._sample_count > 1
            and self._first_source_timestamp is not None
            and self._last_source_timestamp is not None
            and self._last_source_timestamp > self._first_source_timestamp
        ):
            effective_rate = (self._sample_count - 1) / (
                self._last_source_timestamp - self._first_source_timestamp
            )
        metadata = {
            "schema_version": 1,
            "status": status,
            "backend": self.backend.profile.backend,
            "device_profile_id": self.backend.profile.profile_id,
            "configured_sampling_rate_hz": self.backend.profile.sampling_rate_hz,
            "effective_sampling_rate_hz": effective_rate,
            "channel_names": self.backend.channel_names,
            "sample_count": self._sample_count,
            "dropped_batches": self._dropped_batches,
            "dropped_samples": self._dropped_samples,
            "timestamp_discontinuities": self._timestamp_gaps,
            "read_errors": self._read_errors,
            "write_errors": self._write_errors,
            "started_monotonic_seconds": self._started_monotonic,
            "stopped_monotonic_seconds": self._stopped_monotonic,
            "started_wall_time_utc": self._started_wall.isoformat()
            if self._started_wall
            else None,
            "stopped_wall_time_utc": self._stopped_wall.isoformat()
            if self._stopped_wall
            else None,
            "embedded_markers_supported": self.backend.supports_embedded_markers,
            "backend_metadata": self.backend.metadata,
            **details,
        }
        (self.session_path / "acquisition-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
