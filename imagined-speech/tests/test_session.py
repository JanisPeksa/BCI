import hashlib
import json
from pathlib import Path

import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.engine import ProtocolEngine, VirtualClock
from imagined_speech.plan import compile_session_plan
from imagined_speech.session import (
    SessionValidationError,
    SessionWriter,
    validate_session,
)
from imagined_speech.simulation import run_virtual


def create_session(tmp_path: Path) -> tuple[SessionWriter, ProtocolEngine]:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(resolved, plan, "TEST001", tmp_path)
    engine = ProtocolEngine(
        writer.session_id,
        plan,
        resolved.config,
        VirtualClock(),
        writer,
    )
    return writer, engine


def test_complete_session_is_self_describing_and_valid(tmp_path: Path) -> None:
    writer, engine = create_session(tmp_path)

    run_virtual(engine)
    report = validate_session(writer.path)

    assert report.status == "complete"
    assert report.trial_count == 3
    assert report.phase_count == 12
    assert report.event_count == 43
    assert (writer.path / "checksums.sha256").is_file()
    assert (writer.path / "experiment-config.yaml").is_file()
    assert (writer.path / "device-profile.yaml").is_file()
    assert (writer.path / "session-plan.json").is_file()


def test_aborted_session_remains_valid_and_records_action(tmp_path: Path) -> None:
    writer, engine = create_session(tmp_path)
    engine.start()
    engine.abort()

    report = validate_session(writer.path)

    assert report.status == "aborted"
    assert "session_aborted" in (writer.path / "operator-actions.jsonl").read_text(
        encoding="utf-8"
    )


def test_incomplete_session_without_events_is_inspectable(tmp_path: Path) -> None:
    writer, _ = create_session(tmp_path)
    writer.finalize_incomplete()

    report = validate_session(writer.path)

    assert report.status == "incomplete"
    assert report.event_count == 0


def test_checksum_corruption_is_detected(tmp_path: Path) -> None:
    writer, engine = create_session(tmp_path)
    run_virtual(engine)
    with (writer.path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("corruption\n")

    with pytest.raises(SessionValidationError, match="checksum mismatch"):
        validate_session(writer.path)


def test_valid_ids_in_the_wrong_phase_order_are_rejected(tmp_path: Path) -> None:
    writer, engine = create_session(tmp_path)
    run_virtual(engine)
    event_path = writer.path / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    phase_event = next(event for event in events if event["event_type"] == "phase_started")
    phase_event["step_id"] = phase_event["step_id"].replace("phase-rest", "phase-stimulus")
    phase_event["phase"] = "stimulus"
    event_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )

    checksum_path = writer.path / "checksums.sha256"
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    event_digest = hashlib.sha256(event_path.read_bytes()).hexdigest()
    checksum_path.write_text(
        "\n".join(
            f"{event_digest}  events.jsonl" if line.endswith("  events.jsonl") else line
            for line in checksum_lines
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionValidationError, match="phase is out of plan order"):
        validate_session(writer.path)
