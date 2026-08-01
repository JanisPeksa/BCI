from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from run_check import main, parse_args


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    session = tmp_path / "sessions" / "session-1"
    session.mkdir(parents=True)
    onset_mono = 100.0
    onset_wall = datetime(2026, 8, 1, 12, 0, tzinfo=UTC).timestamp()
    (session / "manifest.json").write_text(
        json.dumps({"created_at_utc": "2026-08-01T12:00:00Z", "status": "complete"}),
        encoding="utf-8",
    )
    (session / "experiment-config.yaml").write_text(
        yaml.safe_dump({
            "protocol": {"stimulation_seconds": 10.0},
            "stimuli": [{
                "id": "freq-8-25",
                "frequency_hz": 8.25,
                "phase_offset_radians": 0.0,
                "duty_cycle": 0.5,
            }],
        }),
        encoding="utf-8",
    )
    events = [
        {
            "event_type": "stimulus_onset",
            "presentation_id": "presentation-0001",
            "trial_number": 1,
            "stimulus_id": "freq-8-25",
            "monotonic_timestamp": onset_mono,
            "wall_clock_timestamp_utc": datetime.fromtimestamp(onset_wall, UTC).isoformat(),
            "payload": {"confirmed_by_frame_swap": True},
        },
        {
            "event_type": "stimulus_offset",
            "presentation_id": "presentation-0001",
            "stimulus_id": "freq-8-25",
            "monotonic_timestamp": onset_mono + 10.0,
            "wall_clock_timestamp_utc": datetime.fromtimestamp(onset_wall + 10.0, UTC).isoformat(),
            "payload": {"confirmed_by_frame_swap": True},
        },
    ]
    (session / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    measurement = tmp_path / "measurement"
    measurement.mkdir()
    timestamps = np.arange(99.0, 111.01, 0.01)
    cycles = np.mod((timestamps - onset_mono) * 8.25, 1.0)
    light = np.where(cycles < 0.5, 900, 100)
    with (measurement / "light_amp.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_monotonic", "time_wall", "light_amp"])
        writer.writerows(
            (mono, onset_wall + mono - onset_mono, value)
            for mono, value in zip(timestamps, light, strict=True)
        )
    (measurement / "photosensor_sync.csv").write_text(
        "kind,time_monotonic,time_wall\nstart,99,0\n",
        encoding="utf-8",
    )
    return session, measurement / "light_amp.csv"


def test_explicit_session_writes_self_contained_results(tmp_path: Path) -> None:
    session, light = _write_inputs(tmp_path)

    assert main(["--session", str(session), "--light", str(light), "--latency", "0"]) == 0

    result = session / "photosensor-check"
    expected = {
        "summary.csv",
        "time_domain.png",
        "fft_classes.png",
        "light_amp.csv",
        "photosensor_sync.csv",
    }
    assert expected <= {path.name for path in result.iterdir()}
    summary = pd.read_csv(result / "summary.csv")
    assert bool(summary.loc[0, "pass"])
    assert summary.loc[0, "waveform"] == "square"

    assert main([
        "--session",
        str(session),
        "--light",
        str(result / "light_amp.csv"),
        "--latency",
        "0",
    ]) == 0


def test_latest_session_mode(tmp_path: Path) -> None:
    session, light = _write_inputs(tmp_path)

    assert main([
        "--latest",
        "--session-root",
        str(session.parent),
        "--light",
        str(light),
        "--latency",
        "0",
    ]) == 0
    assert (session / "photosensor-check" / "summary.csv").is_file()


def test_latest_requires_session_root() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--latest", "--light", "light_amp.csv"])


def test_explicit_session_rejects_session_root() -> None:
    with pytest.raises(SystemExit):
        parse_args([
            "--session",
            "session",
            "--session-root",
            "sessions",
            "--light",
            "light_amp.csv",
        ])
