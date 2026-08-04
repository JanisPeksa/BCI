from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.planning import compile_session_plan
from imagined_speech.recording import SessionWriter, validate_session


def _mapping_hash(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_historical_schema_one_package_remains_valid(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    writer = SessionWriter(
        resolved,
        compile_session_plan(resolved.config),
        "LEGACY01",
        tmp_path,
        auto_finalize=False,
    )
    writer.finalize_incomplete()

    config_path = writer.path / "experiment-config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["schema_version"] = 1
    config["presentation"].pop("psychopy")
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    fingerprint = _mapping_hash(config)

    plan_path = writer.path / "session-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["schema_version"] = 1
    plan["config_hash"] = fingerprint
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    manifest_path = writer.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 1
    manifest["config_hash"] = fingerprint
    presentation_names = {"presentation-metadata.json", "presentation-timing.jsonl"}
    manifest["artifacts"] = [
        name for name in manifest["artifacts"] if name not in presentation_names
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for name in presentation_names:
        (writer.path / name).unlink()

    checksum_names = [
        name for name in manifest["artifacts"] if name != "checksums.sha256"
    ]
    (writer.path / "checksums.sha256").write_text(
        "\n".join(
            f"{hashlib.sha256((writer.path / name).read_bytes()).hexdigest()}  {name}"
            for name in sorted(checksum_names)
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_session(writer.path)
    assert report.status == "incomplete"
