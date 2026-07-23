from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ssvep_bci import __version__
from ssvep_bci.acquisition.base import AcquisitionDescriptor, MarkerReceipt, SampleBatch
from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.events.models import ProtocolEvent
from ssvep_bci.planning.models import SessionPlan
from ssvep_bci.recording.raw_csv import RawCsvWriter


PARTICIPANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact(value: dict[str, Any]) -> dict[str, Any]:
    connection = value.get("connection")
    if isinstance(connection, dict):
        fragments = ("password", "secret", "token", "api_key", "credential")
        value["connection"] = {
            key: "[REDACTED]" if any(part in key.lower() for part in fragments) else item
            for key, item in connection.items()
        }
    return value


class SessionRecorder:
    def __init__(
        self,
        resolved: ResolvedExperiment,
        plan: SessionPlan,
        participant_id: str,
        session_label: str | None = None,
    ) -> None:
        if not PARTICIPANT_PATTERN.fullmatch(participant_id):
            raise ValueError("participant ID must use letters, digits, underscores, or hyphens")
        if session_label and not PARTICIPANT_PATTERN.fullmatch(session_label):
            raise ValueError("session label must use letters, digits, underscores, or hyphens")
        self.resolved = resolved
        self.plan = plan
        self.participant_id = participant_id
        self.session_label = session_label
        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now(UTC)
        name = (
            f"{self.created_at.strftime('%Y%m%dT%H%M%SZ')}_{participant_id}_"
            f"{session_label + '_' if session_label else ''}{self.session_id[:8]}"
        )
        resolved.output_root.mkdir(parents=True, exist_ok=True)
        self.path = resolved.output_root / name
        self.path.mkdir()
        self._lock = threading.RLock()
        self._closed = False
        self._descriptor: AcquisitionDescriptor | None = None
        self._raw = RawCsvWriter(
            self.path / "eeg_raw.csv",
            resolved.device.channels,
            resolved.config.output.flush_every_batch,
        )
        self._event_file = (self.path / "events.jsonl").open("a", encoding="utf-8", newline="\n")
        self._marker_file = (self.path / "acquisition-markers.jsonl").open("a", encoding="utf-8", newline="\n")
        self._health_file = (self.path / "acquisition-health.jsonl").open("a", encoding="utf-8", newline="\n")
        self._processing_file = None
        if resolved.config.processing.enabled:
            self._processing_file = (self.path / "processing-results.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            )
        self._artifacts = {
            "manifest.json", "experiment-config.yaml", "device-profile.yaml",
            "session-plan.json", "events.jsonl", "eeg_raw.csv",
            "acquisition-metadata.json", "acquisition-markers.jsonl",
            "acquisition-health.jsonl",
        }
        if self._processing_file:
            self._artifacts.add("processing-results.jsonl")
        self._write_snapshots()
        self._write_manifest("in_progress")
        self._raw.start()

    def set_acquisition_descriptor(self, descriptor: AcquisitionDescriptor) -> None:
        self._descriptor = descriptor
        self.health("acquisition_started", "info", metadata=descriptor.metadata)

    def record_batch(self, batch: SampleBatch) -> None:
        self._raw.submit(batch)

    def emit(self, event: ProtocolEvent) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("session is finalized")
            self._event_file.write(event.model_dump_json() + "\n")
            self._event_file.flush()

    def record_marker(self, receipt: MarkerReceipt) -> None:
        value = {
            "schema_version": 1,
            "record_type": "marker_insert_result",
            "event_id": receipt.request.event_id,
            "event_sequence": receipt.request.event_sequence,
            "event_type": receipt.request.event_type,
            "marker_code": receipt.request.marker_code,
            "event_monotonic_timestamp": receipt.request.event_monotonic_timestamp,
            "attempt_monotonic_timestamp": receipt.attempt_monotonic_timestamp,
            "supported": receipt.supported,
            "success": receipt.success,
            "error": receipt.error,
        }
        with self._lock:
            self._marker_file.write(json.dumps(value, ensure_ascii=False) + "\n")
            self._marker_file.flush()

    def record_processing(self, result: Any) -> None:
        if self._processing_file is None:
            return
        with self._lock:
            if hasattr(result, "model_dump_json"):
                serialized = result.model_dump_json()
            else:
                serialized = json.dumps(result, ensure_ascii=False)
            self._processing_file.write(serialized + "\n")
            self._processing_file.flush()

    def health(self, kind: str, severity: str, **details: Any) -> None:
        value = {
            "schema_version": 1,
            "kind": kind,
            "severity": severity,
            "wall_clock_timestamp_utc": datetime.now(UTC).isoformat(),
            **details,
        }
        with self._lock:
            self._health_file.write(json.dumps(value, ensure_ascii=False) + "\n")
            self._health_file.flush()

    def finalize(self, status: str, error: str | None = None) -> None:
        if status not in {"complete", "aborted", "failed", "incomplete"}:
            raise ValueError(f"invalid terminal status: {status}")
        with self._lock:
            if self._closed:
                return
            raw_error = None
            try:
                self._raw.stop()
            except Exception as exc:
                raw_error = str(exc)
                status = "failed"
            for handle in (
                self._event_file, self._marker_file, self._health_file, self._processing_file
            ):
                if handle is not None and not handle.closed:
                    handle.flush()
                    handle.close()
            descriptor = self._descriptor
            metadata = {
                "schema_version": 1,
                "status": status,
                "backend": descriptor.backend if descriptor else self.resolved.device.backend,
                "device_profile_id": self.resolved.device.profile_id,
                "configured_sampling_rate_hz": self.resolved.device.sampling_rate_hz,
                "channel_names": [channel.label for channel in self.resolved.device.channels],
                "sample_count": self._raw.sample_count,
                "embedded_markers_supported": (
                    descriptor.supports_embedded_markers if descriptor else False
                ),
                "backend_metadata": descriptor.metadata if descriptor else {},
                "error": error or raw_error,
            }
            (self.path / "acquisition-metadata.json").write_text(
                _json(metadata), encoding="utf-8", newline="\n"
            )
            self._write_manifest(status, error or raw_error)
            if self.resolved.config.output.write_checksums:
                lines = [
                    f"{_sha256(self.path / name)}  {name}"
                    for name in sorted(self._artifacts)
                    if (self.path / name).is_file()
                ]
                (self.path / "checksums.sha256").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
                )
            self._closed = True

    def mark_failed_after_validation(self, error: str) -> None:
        """Keep finalized metadata truthful if the post-write audit fails."""
        with self._lock:
            manifest_path = self.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "failed"
            manifest["error"] = error
            manifest["finalized_at_utc"] = datetime.now(UTC).isoformat()
            _atomic_write(manifest_path, _json(manifest))
            metadata_path = self.path / "acquisition-metadata.json"
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["status"] = "failed"
                metadata["error"] = error
                _atomic_write(metadata_path, _json(metadata))
            if self.resolved.config.output.write_checksums:
                lines = [
                    f"{_sha256(self.path / name)}  {name}"
                    for name in sorted(self._artifacts)
                    if (self.path / name).is_file()
                ]
                (self.path / "checksums.sha256").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
                )

    def _write_snapshots(self) -> None:
        config_value = self.resolved.config.model_dump(mode="json")
        device_value = _redact(self.resolved.device.model_dump(mode="json"))
        (self.path / "experiment-config.yaml").write_text(
            yaml.safe_dump(config_value, allow_unicode=True, sort_keys=False),
            encoding="utf-8", newline="\n",
        )
        (self.path / "device-profile.yaml").write_text(
            yaml.safe_dump(device_value, allow_unicode=True, sort_keys=False),
            encoding="utf-8", newline="\n",
        )
        (self.path / "session-plan.json").write_text(
            self.plan.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    def _write_manifest(self, status: str, error: str | None = None) -> None:
        value = {
            "schema_version": 1,
            "software_version": __version__,
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "session_label": self.session_label,
            "experiment_id": self.resolved.config.experiment_id,
            "plan_id": self.plan.plan_id,
            "config_hash": self.plan.config_hash,
            "status": status,
            "created_at_utc": self.created_at.isoformat(),
            "artifacts": sorted(self._artifacts),
            "error": error,
        }
        if status != "in_progress":
            value["finalized_at_utc"] = datetime.now(UTC).isoformat()
        _atomic_write(self.path / "manifest.json", _json(value))


def validate_session(path: str | Path) -> dict[str, Any]:
    session_path = Path(path).expanduser().resolve()
    required = {
        "manifest.json", "experiment-config.yaml", "device-profile.yaml",
        "session-plan.json", "events.jsonl", "eeg_raw.csv",
        "acquisition-metadata.json", "acquisition-markers.jsonl",
        "acquisition-health.jsonl",
    }
    missing = sorted(name for name in required if not (session_path / name).is_file())
    if missing:
        raise ValueError("missing session artifacts: " + ", ".join(missing))
    manifest = json.loads((session_path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    event_count = 0
    for line in (session_path / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)
            event_count += 1
    metadata = json.loads(
        (session_path / "acquisition-metadata.json").read_text(encoding="utf-8")
    )
    with (session_path / "eeg_raw.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle)
        header = next(rows)
        if header[:6] != [
            "sample_index", "receipt_monotonic_timestamp",
            "receipt_wall_clock_timestamp_utc", "source_timestamp",
            "corrected_source_timestamp", "aligned_monotonic_timestamp",
        ]:
            raise ValueError("unexpected raw EEG header")
        sample_count = 0
        for sample_count, row in enumerate(rows, start=1):
            if int(row[0]) != sample_count - 1:
                raise ValueError("raw EEG sample indexes are discontinuous")
    if sample_count != metadata.get("sample_count"):
        raise ValueError("raw EEG sample count does not match metadata")
    checksum_path = session_path / "checksums.sha256"
    if manifest["status"] != "in_progress" and checksum_path.is_file():
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            expected, name = line.split(maxsplit=1)
            if _sha256(session_path / name.strip()) != expected:
                raise ValueError(f"checksum mismatch: {name}")
    return {
        "session_id": manifest["session_id"],
        "status": "incomplete" if manifest["status"] == "in_progress" else manifest["status"],
        "event_count": event_count,
        "sample_count": sample_count,
    }
