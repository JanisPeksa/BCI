from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
import pytest

from imagined_speech.cli import default_config_path
from imagined_speech.config import load_experiment
from imagined_speech.planning import compile_session_plan
from imagined_speech.recording import SessionWriter, validate_session


def _mapping_hash(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("schema_version", [1, 2])
def test_historical_package_remains_valid(
    tmp_path: Path, schema_version: int
) -> None:
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
    config["schema_version"] = schema_version
    experiment = config["protocol"].pop("experiment")
    practice = config["protocol"].pop("practice")
    config["protocol"].update({
        "blocks": experiment["blocks"],
        "repetitions_per_stimulus": experiment["repetitions_per_stimulus"],
        "practice_stimulus_ids": practice["stimulus_ids"],
        "practice_repetitions_per_stimulus": practice["repetitions_per_stimulus"],
    })
    if schema_version == 1:
        config["presentation"].pop("psychopy")
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    fingerprint = _mapping_hash(config)

    plan_path = writer.path / "session-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["schema_version"] = schema_version
    plan["config_hash"] = fingerprint
    legacy_items = []
    for item in plan["items"]:
        if item["kind"] not in {"practice", "experiment_block"}:
            legacy_items.append(item)
            continue
        practice_item = item["kind"] == "practice"
        block_id = item.get("practice_id", item.get("block_id"))
        trials = []
        for trial in item["trials"]:
            legacy_trial = dict(trial)
            legacy_trial["block_id"] = block_id
            legacy_trial["block_trial_number"] = legacy_trial.pop(
                "group_trial_number"
            )
            trials.append(legacy_trial)
        legacy_items.append({
            "kind": "block",
            "block_id": block_id,
            "block_type": "practice" if practice_item else "experiment",
            "block_number": 0 if practice_item else item["block_number"],
            "block_count": experiment["blocks"],
            "trials": trials,
        })
    plan["items"] = legacy_items
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    manifest_path = writer.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = schema_version
    manifest["config_hash"] = fingerprint
    presentation_names = {"presentation-metadata.json", "presentation-timing.jsonl"}
    if schema_version == 1:
        manifest["artifacts"] = [
            name for name in manifest["artifacts"] if name not in presentation_names
        ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if schema_version == 1:
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
