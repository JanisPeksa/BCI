"""Append-only session packages and reconstruction validation."""

from __future__ import annotations

import hashlib
import csv
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from imagined_speech import __version__
from imagined_speech.config import DeviceProfile, ExperimentConfig, ResolvedExperiment
from imagined_speech.events import EventSink, EventType, ProtocolEvent
from imagined_speech.runtime.commands import (
    OperatorCommand,
    OperatorCommandRecord,
    OperatorCommandStatus,
)
from imagined_speech.planning import (
    BlockPlan,
    BreakPlan,
    RestPlan,
    SessionPlan,
    config_fingerprint,
)


PARTICIPANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TERMINAL_EVENT_STATUS = {
    EventType.SESSION_COMPLETED: "complete",
    EventType.SESSION_ABORTED: "aborted",
    EventType.SESSION_FAILED: "failed",
}
ACTION_EVENTS = {
    EventType.SESSION_PAUSED,
    EventType.SESSION_RESUMED,
    EventType.TRIAL_REPEATED,
    EventType.BLOCK_REPEATED,
    EventType.REFIT_RECORDED,
    EventType.SESSION_ABORTED,
    EventType.SESSION_FAILED,
}


class SessionValidationError(ValueError):
    """Raised when a session package is inconsistent or corrupted."""


@dataclass(frozen=True)
class SessionValidationReport:
    session_path: Path
    session_id: str
    status: str
    event_count: int
    trial_count: int
    phase_count: int
    sample_count: int = 0
    warnings: tuple[str, ...] = ()
    operator_command_count: int = 0


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _redact_connection_secrets(device_data: dict[str, Any]) -> dict[str, Any]:
    connection = device_data.get("connection")
    if not isinstance(connection, dict):
        return device_data
    sensitive_fragments = ("password", "secret", "token", "api_key", "credential")
    device_data["connection"] = {
        key: "[REDACTED]"
        if any(fragment in key.lower() for fragment in sensitive_fragments)
        else value
        for key, value in connection.items()
    }
    return device_data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_mapping_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SessionWriter(EventSink):
    """Writes events immediately and finalizes a versioned session package."""

    def __init__(
        self,
        resolved: ResolvedExperiment,
        plan: SessionPlan,
        participant_id: str,
        output_root: Path | None = None,
        *,
        auto_finalize: bool = True,
        session_label: str | None = None,
    ) -> None:
        if not PARTICIPANT_PATTERN.fullmatch(participant_id):
            raise ValueError(
                "participant ID must be 1-64 letters, digits, underscores, or hyphens"
            )
        if plan.config_hash != config_fingerprint(resolved.config):
            raise ValueError("session plan does not match experiment configuration")
        if session_label is not None and not PARTICIPANT_PATTERN.fullmatch(session_label):
            raise ValueError(
                "session label must be 1-64 letters, digits, underscores, or hyphens"
            )

        self.resolved = resolved
        self.plan = plan
        self.participant_id = participant_id
        self.session_label = session_label
        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now(UTC)
        root = (output_root or resolved.output_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        directory_name = (
            f"{self.created_at.strftime('%Y%m%dT%H%M%SZ')}_"
            f"{participant_id}_"
            f"{session_label + '_' if session_label else ''}{self.session_id[:8]}"
        )
        self.path = root / directory_name
        self.path.mkdir(parents=False, exist_ok=False)

        self._lock = threading.RLock()
        self._closed = False
        self._auto_finalize = auto_finalize
        self._operator_sequence = 0
        self._artifacts = {
            "manifest.json",
            "experiment-config.yaml",
            "device-profile.yaml",
            "session-plan.json",
            "events.jsonl",
            "operator-actions.jsonl",
            "presentation-metadata.json",
            "presentation-timing.jsonl",
        }
        self._event_path = self.path / "events.jsonl"
        self._action_path = self.path / "operator-actions.jsonl"
        self._event_file = self._event_path.open("a", encoding="utf-8", newline="\n")
        self._action_file = self._action_path.open("a", encoding="utf-8", newline="\n")
        self._presentation_timing_file = (
            self.path / "presentation-timing.jsonl"
        ).open("a", encoding="utf-8", newline="\n")
        self._presentation_metadata: dict[str, Any] = {
            "schema_version": 2,
            "driver": "pending",
        }
        _write_bytes_atomic(
            self.path / "presentation-metadata.json",
            _json_bytes(self._presentation_metadata),
        )

        self._write_snapshots()
        self._write_manifest("in_progress")

    def _write_snapshots(self) -> None:
        config_data = self.resolved.config.model_dump(mode="json")
        device_data = _redact_connection_secrets(
            self.resolved.device.model_dump(mode="json")
        )
        (self.path / "experiment-config.yaml").write_text(
            yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
        (self.path / "device-profile.yaml").write_text(
            yaml.safe_dump(device_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
        (self.path / "session-plan.json").write_bytes(
            _json_bytes(self.plan.model_dump(mode="json"))
        )

    def _manifest(self, status: str) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "schema_version": 2,
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
        }
        if status != "in_progress":
            manifest["finalized_at_utc"] = datetime.now(UTC).isoformat()
            manifest["artifacts"] = sorted(self._artifacts | {"checksums.sha256"})
        return manifest

    def _write_manifest(self, status: str) -> None:
        _write_bytes_atomic(self.path / "manifest.json", _json_bytes(self._manifest(status)))

    def emit(self, event: ProtocolEvent) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot write to a finalized session")
            serialized = event.model_dump_json() + "\n"
            self._event_file.write(serialized)
            self._event_file.flush()
            if event.event_type in ACTION_EVENTS:
                self._action_file.write(serialized)
                self._action_file.flush()
            terminal_status = TERMINAL_EVENT_STATUS.get(event.event_type)
            if terminal_status is not None and self._auto_finalize:
                self._finalize_locked(terminal_status)

    def register_artifact(self, relative_name: str) -> None:
        normalized = Path(relative_name)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("session artifact path must remain inside the package")
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot register an artifact after finalization")
            if not (self.path / normalized).is_file():
                raise ValueError(f"session artifact does not exist: {relative_name}")
            self._artifacts.add(normalized.as_posix())

    def record_presentation_metadata(self, value: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot write to a finalized session")
            self._presentation_metadata.update(value)
            _write_bytes_atomic(
                self.path / "presentation-metadata.json",
                _json_bytes(self._presentation_metadata),
            )

    def record_presentation_timing(self, value: dict[str, Any]) -> None:
        record = {"schema_version": 2, **value}
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot write to a finalized session")
            self._presentation_timing_file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )
            self._presentation_timing_file.flush()

    def record_frame_intervals(self, values: tuple[float, ...] | list[float]) -> None:
        self._record_interval_csv("frame-intervals.csv", values)

    def record_preflight_frame_intervals(
        self, values: tuple[float, ...] | list[float]
    ) -> None:
        self._record_interval_csv("preflight-frame-intervals.csv", values)

    def _record_interval_csv(
        self,
        filename: str,
        values: tuple[float, ...] | list[float],
    ) -> None:
        path = self.path / filename
        rows = ["frame_index,interval_seconds"]
        rows.extend(f"{index},{value:.12g}" for index, value in enumerate(values))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        self._artifacts.add(filename)

    def record_operator_command(
        self,
        *,
        command: OperatorCommand,
        status: OperatorCommandStatus,
        source: str,
        monotonic_seconds: float,
        wall_time_utc: datetime,
        reason: str,
        state_before: str,
        resulting_state: str,
        note: str | None = None,
        block_id: str | None = None,
        trial_id: str | None = None,
        attempt: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> OperatorCommandRecord:
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot write to a finalized session")
            self._operator_sequence += 1
            record = OperatorCommandRecord(
                sequence_number=self._operator_sequence,
                command=command,
                status=status,
                source=source,
                monotonic_seconds=monotonic_seconds,
                wall_time_utc=wall_time_utc,
                reason=reason,
                note=note,
                state_before=state_before,
                resulting_state=resulting_state,
                session_id=self.session_id,
                block_id=block_id,
                trial_id=trial_id,
                attempt=attempt,
                payload=payload or {},
            )
            self._action_file.write(record.model_dump_json() + "\n")
            self._action_file.flush()
            return record

    def finalize(self, status: str) -> None:
        if status not in {"complete", "aborted", "failed", "incomplete"}:
            raise ValueError(f"unsupported final session status: {status}")
        with self._lock:
            if not self._closed:
                self._finalize_locked(status)

    def finalize_incomplete(self) -> None:
        with self._lock:
            if not self._closed:
                self._finalize_locked("incomplete")

    def _finalize_locked(self, status: str) -> None:
        self._event_file.flush()
        self._action_file.flush()
        self._presentation_timing_file.flush()
        self._event_file.close()
        self._action_file.close()
        self._presentation_timing_file.close()
        self._write_manifest(status)

        checksum_files = sorted(self._artifacts)
        lines = [f"{_sha256(self.path / name)}  {name}" for name in checksum_files]
        (self.path / "checksums.sha256").write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
        )
        self._closed = True


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionValidationError(f"cannot read valid JSON from {path.name}: {exc}") from exc


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SessionValidationError(f"cannot read valid YAML from {path.name}: {exc}") from exc


def _load_events(path: Path) -> list[ProtocolEvent]:
    events: list[ProtocolEvent] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionValidationError(f"cannot read {path.name}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            events.append(ProtocolEvent.model_validate_json(line))
        except ValidationError as exc:
            raise SessionValidationError(
                f"invalid event at {path.name}:{line_number}: {exc}"
            ) from exc
    return events


def _verify_checksums(session_path: Path, required: bool) -> tuple[str, ...]:
    checksum_path = session_path / "checksums.sha256"
    if not checksum_path.is_file():
        if required:
            raise SessionValidationError("checksums.sha256 is missing")
        return ("checksums are unavailable for an unfinalized session",)

    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionValidationError(f"cannot read checksums.sha256: {exc}") from exc
    for line in lines:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise SessionValidationError("malformed checksum entry")
        expected, relative_name = parts
        artifact = session_path / relative_name.strip()
        if not artifact.is_file():
            raise SessionValidationError(f"checksummed artifact is missing: {relative_name}")
        if _sha256(artifact) != expected:
            raise SessionValidationError(f"checksum mismatch: {relative_name}")
    return ()


def _validate_event_structure(
    events: list[ProtocolEvent], plan: SessionPlan, status: str
) -> tuple[int, int]:
    if not events:
        if status == "incomplete":
            return 0, 0
        raise SessionValidationError("event log is empty")

    block_ids = {block.block_id for block in plan.blocks}
    trials_by_block = {
        block.block_id: tuple(trial.trial_id for trial in block.trials)
        for block in plan.blocks
    }
    trial_ids = {trial_id for trials in trials_by_block.values() for trial_id in trials}
    phases_by_trial = {
        trial.trial_id: tuple(phase.step_id for phase in trial.phases)
        for block in plan.blocks
        for trial in block.trials
    }
    phase_types = {
        phase.step_id: phase.phase
        for block in plan.blocks
        for trial in block.trials
        for phase in trial.phases
    }
    phase_ids = set(phase_types)
    rest_ids = {item.rest_id for item in plan.items if isinstance(item, RestPlan)}
    break_ids = {item.break_id for item in plan.items if isinstance(item, BreakPlan)}
    item_order = [
        (
            item.kind,
            item.rest_id
            if isinstance(item, RestPlan)
            else item.break_id
            if isinstance(item, BreakPlan)
            else item.block_id,
        )
        for item in plan.items
    ]

    active_block: str | None = None
    active_trial: str | None = None
    active_attempt: int | None = None
    active_phase: str | None = None
    active_rest: str | None = None
    active_break: str | None = None
    paused = False
    terminal: EventType | None = None
    ended_trials: set[str] = set()
    ended_phases: set[str] = set()
    item_position = 0
    trial_positions = {block_id: 0 for block_id in block_ids}
    phase_positions: dict[tuple[str, int], int] = {}
    latest_attempt = {trial_id: 0 for trial_id in trial_ids}
    stimulus_seen = False

    def expect_item(kind: str, identifier: str | None) -> None:
        if item_position >= len(item_order) or item_order[item_position] != (
            kind,
            identifier,
        ):
            raise SessionValidationError(
                f"session item is out of plan order: {kind} {identifier}"
            )

    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence_number != expected_sequence:
            raise SessionValidationError(
                f"event sequence gap at {expected_sequence}: found {event.sequence_number}"
            )
        if event.plan_id != plan.plan_id:
            raise SessionValidationError("event references a different session plan")
        if terminal is not None:
            raise SessionValidationError("events appear after the terminal event")

        event_type = event.event_type
        if event_type == EventType.SESSION_STARTED:
            if expected_sequence != 1:
                raise SessionValidationError("session_started must be the first event")
        elif event_type == EventType.REST_STARTED:
            if active_block or active_break or active_rest:
                raise SessionValidationError("rest started while another session scope was active")
            if event.step_id not in rest_ids:
                raise SessionValidationError("rest event references an unknown plan item")
            expect_item("rest", event.step_id)
            active_rest = event.step_id
        elif event_type == EventType.REST_ENDED:
            if event.step_id != active_rest:
                raise SessionValidationError("rest end does not match active rest")
            active_rest = None
            item_position += 1
        elif event_type == EventType.BLOCK_STARTED:
            if active_block or active_break or active_rest:
                raise SessionValidationError("block started while another scope was active")
            if event.block_id not in block_ids:
                raise SessionValidationError("block event references an unknown block")
            expect_item("block", event.block_id)
            active_block = event.block_id
        elif event_type == EventType.BLOCK_ENDED:
            if event.block_id != active_block or active_trial:
                raise SessionValidationError("block end does not match active block")
            active_block = None
            item_position += 1
        elif event_type == EventType.BREAK_STARTED:
            if active_block or active_break or active_rest:
                raise SessionValidationError("break started while another scope was active")
            if event.step_id not in break_ids:
                raise SessionValidationError("break event references an unknown plan item")
            expect_item("break", event.step_id)
            active_break = event.step_id
        elif event_type == EventType.BREAK_ENDED:
            if event.step_id != active_break:
                raise SessionValidationError("break end does not match active break")
            active_break = None
            item_position += 1
        elif event_type == EventType.TRIAL_STARTED:
            if not active_block or active_trial:
                raise SessionValidationError("trial started outside a block")
            if event.trial_id not in trial_ids:
                raise SessionValidationError("trial event references an unknown trial")
            expected_trials = trials_by_block[active_block]
            position = trial_positions[active_block]
            if position >= len(expected_trials) or event.trial_id != expected_trials[position]:
                raise SessionValidationError("trial is out of plan order or in the wrong block")
            if event.attempt != latest_attempt[event.trial_id] + 1:
                raise SessionValidationError("trial attempt sequence is discontinuous")
            latest_attempt[event.trial_id] = event.attempt
            active_trial = event.trial_id
            active_attempt = event.attempt
            phase_positions[(event.trial_id, event.attempt)] = 0
        elif event_type == EventType.TRIAL_ENDED:
            if (
                event.trial_id != active_trial
                or event.attempt != active_attempt
                or active_phase
            ):
                raise SessionValidationError("trial end does not match active trial")
            assert event.trial_id is not None
            assert active_block is not None
            outcome = event.payload.get("outcome", "completed")
            if outcome == "completed":
                ended_trials.add(event.trial_id)
                trial_positions[active_block] += 1
            elif outcome not in {"superseded", "aborted", "failed"}:
                raise SessionValidationError(f"unknown trial outcome: {outcome}")
            active_trial = None
            active_attempt = None
        elif event_type == EventType.PHASE_STARTED:
            if not active_trial or active_phase:
                raise SessionValidationError("phase started outside a trial")
            if event.step_id not in phase_ids:
                raise SessionValidationError("phase event references an unknown phase")
            expected_phases = phases_by_trial[active_trial]
            if event.attempt != active_attempt or active_attempt is None:
                raise SessionValidationError("phase attempt does not match active trial")
            position = phase_positions[(active_trial, active_attempt)]
            if position >= len(expected_phases) or event.step_id != expected_phases[position]:
                raise SessionValidationError("phase is out of plan order or in the wrong trial")
            if event.phase != phase_types[event.step_id]:
                raise SessionValidationError("phase type does not match the session plan")
            active_phase = event.step_id
            stimulus_seen = False
        elif event_type == EventType.STIMULUS_PRESENTED:
            if event.step_id != active_phase or event.phase != "stimulus":
                raise SessionValidationError("stimulus event is outside the stimulus phase")
            if stimulus_seen:
                raise SessionValidationError("stimulus was presented twice in one phase")
            stimulus_seen = True
        elif event_type == EventType.PHASE_ENDED:
            if event.step_id != active_phase:
                raise SessionValidationError("phase end does not match active phase")
            if event.phase == "stimulus" and not stimulus_seen:
                raise SessionValidationError("stimulus phase has no presentation event")
            assert event.step_id is not None
            assert active_trial is not None
            assert active_attempt is not None
            outcome = event.payload.get("outcome", "completed")
            if outcome == "completed":
                ended_phases.add(event.step_id)
                phase_positions[(active_trial, active_attempt)] += 1
            elif outcome not in {"superseded", "aborted", "failed"}:
                raise SessionValidationError(f"unknown phase outcome: {outcome}")
            active_phase = None
        elif event_type == EventType.TRIAL_REPEATED:
            if not active_block or active_trial or active_phase:
                raise SessionValidationError("trial repeat occurred outside a recoverable block")
            expected_trials = trials_by_block[active_block]
            position = trial_positions[active_block]
            if position >= len(expected_trials) or event.trial_id != expected_trials[position]:
                raise SessionValidationError("trial repeat does not match the expected trial")
            assert event.trial_id is not None
            ended_trials.discard(event.trial_id)
            ended_phases.difference_update(phases_by_trial[event.trial_id])
        elif event_type == EventType.BLOCK_REPEATED:
            if event.block_id != active_block or active_trial or active_phase:
                raise SessionValidationError("block repeat does not match the active block")
            assert active_block is not None
            trial_positions[active_block] = 0
            for trial_id in trials_by_block[active_block]:
                ended_trials.discard(trial_id)
                ended_phases.difference_update(phases_by_trial[trial_id])
        elif event_type == EventType.REFIT_RECORDED:
            if not (active_block or active_rest or active_break):
                raise SessionValidationError("refit was recorded outside an active session scope")
        elif event_type == EventType.SESSION_PAUSED:
            if paused:
                raise SessionValidationError("session was paused twice")
            paused = True
        elif event_type == EventType.SESSION_RESUMED:
            if not paused:
                raise SessionValidationError("session resumed while not paused")
            paused = False
        elif event_type in TERMINAL_EVENT_STATUS:
            terminal = event_type

    expected_terminal = {
        "complete": EventType.SESSION_COMPLETED,
        "aborted": EventType.SESSION_ABORTED,
        "failed": EventType.SESSION_FAILED,
    }.get(status)
    if expected_terminal is not None and terminal != expected_terminal:
        raise SessionValidationError(
            f"manifest status {status} does not match terminal event"
        )
    if status == "complete":
        if active_block or active_trial or active_phase or active_rest or active_break or paused:
            raise SessionValidationError("complete session has unclosed event scopes")
        if item_position != len(item_order):
            raise SessionValidationError("complete session does not contain every plan item")
        if ended_trials != trial_ids:
            raise SessionValidationError("complete session does not contain every planned trial")
        if ended_phases != phase_ids:
            raise SessionValidationError("complete session does not contain every planned phase")
    return len(ended_trials), len(ended_phases)


def validate_session(path: str | Path) -> SessionValidationReport:
    session_path = Path(path).expanduser().resolve()
    if not session_path.is_dir():
        raise SessionValidationError(f"session directory does not exist: {session_path}")

    required_files = (
        "manifest.json",
        "experiment-config.yaml",
        "device-profile.yaml",
        "session-plan.json",
        "events.jsonl",
        "operator-actions.jsonl",
    )
    missing = [name for name in required_files if not (session_path / name).is_file()]
    if missing:
        raise SessionValidationError("missing session artifacts: " + ", ".join(missing))

    manifest = _read_json(session_path / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
        raise SessionValidationError("manifest schema version is unsupported")
    status = manifest.get("status")
    if status not in {"in_progress", "incomplete", "complete", "aborted", "failed"}:
        raise SessionValidationError(f"unknown manifest status: {status}")
    if manifest.get("schema_version") == 2:
        presentation_files = (
            "presentation-metadata.json",
            "presentation-timing.jsonl",
        )
        missing_presentation = [
            name for name in presentation_files if not (session_path / name).is_file()
        ]
        if missing_presentation:
            raise SessionValidationError(
                "missing presentation artifacts: " + ", ".join(missing_presentation)
            )
        metadata = _read_json(session_path / "presentation-metadata.json")
        if not isinstance(metadata, dict) or metadata.get("schema_version") not in {1, 2}:
            raise SessionValidationError("presentation metadata schema is unsupported")
        for line_number, line in enumerate(
            (session_path / "presentation-timing.jsonl").read_text(
                encoding="utf-8"
            ).splitlines(),
            start=1,
        ):
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise SessionValidationError(
                    f"invalid presentation timing record at line {line_number}"
                ) from exc

    warnings = _verify_checksums(session_path, required=status != "in_progress")
    config_data = _read_yaml(session_path / "experiment-config.yaml")
    try:
        if config_data.get("schema_version") == 2:
            config = ExperimentConfig.model_validate(config_data)
            snapshot_hash = config_fingerprint(config)
        elif config_data.get("schema_version") == 1:
            snapshot_hash = _config_mapping_fingerprint(config_data)
        else:
            raise SessionValidationError("experiment snapshot schema is unsupported")
        DeviceProfile.model_validate(_read_yaml(session_path / "device-profile.yaml"))
        plan = SessionPlan.model_validate(_read_json(session_path / "session-plan.json"))
    except ValidationError as exc:
        raise SessionValidationError(f"invalid session snapshot: {exc}") from exc

    if snapshot_hash != plan.config_hash:
        raise SessionValidationError("configuration snapshot hash does not match plan")
    if manifest.get("config_hash") != plan.config_hash:
        raise SessionValidationError("manifest configuration hash does not match plan")
    if manifest.get("plan_id") != plan.plan_id:
        raise SessionValidationError("manifest plan ID does not match plan")

    events = _load_events(session_path / "events.jsonl")
    for event in events:
        if event.session_id != manifest.get("session_id"):
            raise SessionValidationError("event references a different session ID")
    trial_count, phase_count = _validate_event_structure(events, plan, status)
    operator_command_count = _validate_operator_actions(
        session_path / "operator-actions.jsonl",
        events,
        str(manifest["session_id"]),
    )
    sample_count = _validate_acquisition_artifacts(session_path, manifest, events)
    return SessionValidationReport(
        session_path=session_path,
        session_id=str(manifest["session_id"]),
        status=status,
        event_count=len(events),
        trial_count=trial_count,
        phase_count=phase_count,
        sample_count=sample_count,
        operator_command_count=operator_command_count,
        warnings=warnings,
    )


def _validate_operator_actions(
    path: Path, events: list[ProtocolEvent], session_id: str
) -> int:
    events_by_sequence = {event.sequence_number: event for event in events}
    command_count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionValidationError(f"cannot read operator actions: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionValidationError(
                f"invalid operator action at line {line_number}"
            ) from exc
        try:
            if value.get("record_type") == "operator_command":
                record = OperatorCommandRecord.model_validate(value)
                command_count += 1
                if record.sequence_number != command_count:
                    raise SessionValidationError("operator command sequence is discontinuous")
                if record.session_id != session_id:
                    raise SessionValidationError("operator command references another session")
            else:
                event = ProtocolEvent.model_validate(value)
                authoritative = events_by_sequence.get(event.sequence_number)
                if authoritative is None or event != authoritative:
                    raise SessionValidationError(
                        "operator action event does not match the protocol timeline"
                    )
        except ValidationError as exc:
            raise SessionValidationError(
                f"invalid operator action schema at line {line_number}: {exc}"
            ) from exc
    return command_count


def _validate_acquisition_artifacts(
    session_path: Path, manifest: dict[str, Any], events: list[ProtocolEvent]
) -> int:
    acquisition_files = {
        "eeg_raw.csv",
        "acquisition-markers.jsonl",
        "acquisition-health.jsonl",
        "acquisition-metadata.json",
    }
    declared = set(manifest.get("artifacts", []))
    present = {name for name in acquisition_files if (session_path / name).is_file()}
    if not present and not (declared & acquisition_files):
        return 0
    if present != acquisition_files or not acquisition_files <= declared:
        if manifest.get("status") not in {"failed", "incomplete", "in_progress"}:
            raise SessionValidationError("acquisition artifact set is incomplete")
        if not present <= declared:
            raise SessionValidationError("acquisition artifacts are not declared in the manifest")
        if "acquisition-metadata.json" in present:
            metadata = _read_json(session_path / "acquisition-metadata.json")
            return int(metadata.get("sample_count", 0)) if isinstance(metadata, dict) else 0
        return 0

    metadata = _read_json(session_path / "acquisition-metadata.json")
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise SessionValidationError("acquisition metadata schema is unsupported")
    channel_names = metadata.get("channel_names")
    if not isinstance(channel_names, list) or not channel_names:
        raise SessionValidationError("acquisition metadata has no channel catalog")

    raw_path = session_path / "eeg_raw.csv"
    try:
        with raw_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            expected_header = [
                "sample_index",
                "receipt_monotonic_seconds",
                "receipt_time_utc",
                "source_timestamp",
                "corrected_source_timestamp",
                *channel_names,
            ]
            if header != expected_header:
                raise SessionValidationError("raw EEG header does not match acquisition metadata")
            row_count = 0
            expected_width = len(expected_header)
            for row_count, row in enumerate(reader, start=1):
                if len(row) != expected_width:
                    raise SessionValidationError(
                        f"raw EEG row {row_count} has an unexpected column count"
                    )
                if int(row[0]) != row_count - 1:
                    raise SessionValidationError("raw EEG sample indexes are discontinuous")
    except OSError as exc:
        raise SessionValidationError(f"cannot read eeg_raw.csv: {exc}") from exc
    if row_count != metadata.get("sample_count"):
        raise SessionValidationError("raw EEG sample count does not match metadata")
    if manifest.get("status") == "complete" and row_count == 0:
        raise SessionValidationError("complete acquired session contains no EEG samples")

    events_by_sequence = {event.sequence_number: event for event in events}
    marker_path = session_path / "acquisition-markers.jsonl"
    try:
        marker_lines = marker_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionValidationError(f"cannot read acquisition markers: {exc}") from exc
    for line_number, line in enumerate(marker_lines, start=1):
        try:
            marker = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionValidationError(
                f"invalid acquisition marker at line {line_number}"
            ) from exc
        event = events_by_sequence.get(marker.get("event_sequence"))
        if event is None or event.marker_code != marker.get("marker_code"):
            raise SessionValidationError("acquisition marker does not match protocol event")
    if len(marker_lines) != len(events):
        raise SessionValidationError("not every protocol event has an acquisition marker record")

    try:
        health_lines = (session_path / "acquisition-health.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as exc:
        raise SessionValidationError(f"cannot read acquisition health log: {exc}") from exc
    for line_number, line in enumerate(health_lines, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionValidationError(
                f"invalid acquisition health log near line {line_number}"
            ) from exc
    return row_count
