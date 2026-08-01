from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from photosensor_check.analysis import (
    build_trials,
    calibrate_sfreq,
    detect_peak,
    find_latest_session,
    ideal_waveform,
    load_session_trials,
    power_spectrum,
    retime_uniform,
    sampling_diagnostics,
)


def _write_session(
    path: Path,
    *,
    created_at: str = "2026-08-01T12:00:00Z",
    waveform: str | None = None,
    confirmed_offset: bool = True,
) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"created_at_utc": created_at, "status": "in_progress"}),
        encoding="utf-8",
    )
    stimulus = {
        "id": "target",
        "frequency_hz": 12.75,
        "phase_offset_radians": 0.25,
        "duty_cycle": 0.4,
    }
    if waveform is not None:
        stimulus["waveform"] = waveform
    (path / "experiment-config.yaml").write_text(
        yaml.safe_dump({
            "protocol": {"stimulation_seconds": 10.0},
            "stimuli": [stimulus],
        }),
        encoding="utf-8",
    )
    events = [
        {
            "event_type": "stimulus_onset",
            "presentation_id": "presentation-0001",
            "trial_number": 1,
            "stimulus_id": "target",
            "monotonic_timestamp": 100.0,
            "wall_clock_timestamp_utc": "2026-08-01T12:00:10Z",
            "payload": {"confirmed_by_frame_swap": True},
        },
        {
            "event_type": "stimulus_offset",
            "presentation_id": "presentation-0001",
            "monotonic_timestamp": 110.0,
            "wall_clock_timestamp_utc": "2026-08-01T12:00:20Z",
            "payload": {"confirmed_by_frame_swap": confirmed_offset},
        },
    ]
    (path / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    return path


def test_loads_ssvep_bci_square_default(tmp_path: Path) -> None:
    session = _write_session(tmp_path / "session")
    trials, duration = load_session_trials(session)

    assert duration == 10.0
    assert len(trials) == 1
    assert trials[0].waveform == "square"
    assert trials[0].offset_monotonic == 110.0


def test_loads_psychopy_waveform_and_ignores_unconfirmed_offset(tmp_path: Path) -> None:
    session = _write_session(
        tmp_path / "session",
        waveform="sinusoidal",
        confirmed_offset=False,
    )
    trials, _ = load_session_trials(session)

    assert trials[0].waveform == "sinusoidal"
    assert trials[0].offset_monotonic is None


def test_ideal_waveform_matches_square_and_sinusoidal(tmp_path: Path) -> None:
    square, _ = load_session_trials(_write_session(tmp_path / "square"))
    sine, _ = load_session_trials(
        _write_session(tmp_path / "sine", waveform="sinusoidal")
    )
    timestamps = np.array([100.0, 100.01, 100.02])

    assert set(ideal_waveform(square[0], timestamps)) <= {-1.0, 1.0}
    np.testing.assert_allclose(
        ideal_waveform(sine[0], timestamps),
        np.sin(2 * np.pi * 12.75 * (timestamps - 100.0) + 0.25),
    )


def test_find_latest_session_uses_manifest_time_and_allows_any_status(tmp_path: Path) -> None:
    older = _write_session(tmp_path / "older", created_at="2026-08-01T12:00:00Z")
    newer = _write_session(tmp_path / "newer", created_at="2026-08-01T13:00:00Z")
    manifest = json.loads((newer / "manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "failed"
    (newer / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    for name in ("events.jsonl", "experiment-config.yaml"):
        (malformed / name).write_text("", encoding="utf-8")
    (malformed / "manifest.json").write_text("not json", encoding="utf-8")

    assert find_latest_session(tmp_path) == newer.resolve()
    assert older.exists()


def test_find_latest_session_rejects_empty_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no usable session"):
        find_latest_session(tmp_path)


def test_device_timing_is_independent_of_slow_host_receipts() -> None:
    sample_index = np.arange(8000)
    device_time_us = sample_index * 2500
    device_time = device_time_us * 1e-6
    # Simulate a host reader that drains a 400 Hz device stream at only 340 Hz.
    host_time = 100.0 + sample_index / 340.0
    frame = pd.DataFrame({
        "time_monotonic": host_time,
        "time_wall": 1_800_000_000.0 + host_time,
        "light_amp": np.sin(2 * np.pi * 12.75 * device_time),
        "sample_index": sample_index,
        "device_time_us": device_time_us,
    })

    diagnostics = sampling_diagnostics(frame)
    sfreq, retimed = retime_uniform(frame)

    assert diagnostics["timing_source"] == "device"
    assert diagnostics["span_rate_hz"] == pytest.approx(340.0)
    assert diagnostics["device_rate_hz"] == pytest.approx(400.0)
    assert diagnostics["missing_device_samples"] == 0
    assert calibrate_sfreq(frame) == pytest.approx(400.0)
    assert sfreq == pytest.approx(400.0)
    assert retimed["time_monotonic"].iloc[-1] - retimed["time_monotonic"].iloc[0] \
        == pytest.approx(device_time[-1])
    freqs, psd = power_spectrum(retimed["light_amp"].to_numpy(), sfreq)
    assert detect_peak(freqs, psd) == pytest.approx(12.75, abs=0.02)


def test_device_timing_handles_micros_wrap_and_missing_index() -> None:
    frame = pd.DataFrame({
        "time_monotonic": [100.0, 100.003, 100.006, 100.009],
        "time_wall": [200.0, 200.003, 200.006, 200.009],
        "light_amp": [0, 1, 0, 1],
        "sample_index": [10, 11, 13, 14],
        "device_time_us": [2**32 - 5000, 2**32 - 2500, 0, 2500],
    })

    diagnostics = sampling_diagnostics(frame)

    assert diagnostics["device_rate_hz"] == pytest.approx(400.0)
    assert diagnostics["missing_device_samples"] == 1
