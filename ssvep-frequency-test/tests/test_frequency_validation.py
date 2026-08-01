from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import frequency_validation
import run_check


def test_run_uses_selected_python_and_explicit_output_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, check):
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(frequency_validation.subprocess, "run", fake_run)
    session_root = tmp_path / "portable-sessions"

    result = frequency_validation.main([
        "run",
        "ssvep-bci",
        "--workspace", str(tmp_path),
        "--python", "custom-python",
        "--session-root", str(session_root),
        "--participant", "SCREEN_TEST",
        "--windowed",
    ])

    assert result == 0
    assert calls == [[
        "custom-python",
        "-m", "ssvep_bci",
        "run",
        "--config", "frequency-validation",
        "--participant", "SCREEN_TEST",
        "--session-label", "frequency-validation",
        "--output-root", str(session_root.resolve()),
        "--windowed",
    ]]


def test_analyze_selects_newest_measurement(tmp_path: Path, monkeypatch) -> None:
    older = tmp_path / "measurements" / "older"
    newer = tmp_path / "measurements" / "newer"
    older.mkdir(parents=True)
    newer.mkdir()
    (older / "light_amp.csv").write_text("old", encoding="utf-8")
    (newer / "light_amp.csv").write_text("new", encoding="utf-8")
    os.utime(older / "light_amp.csv", (1, 1))
    os.utime(newer / "light_amp.csv", (2, 2))
    calls: list[list[str]] = []

    def fake_check(argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(run_check, "main", fake_check)

    assert frequency_validation.main([
        "analyze",
        "psychopy-ssvep",
        "--workspace", str(tmp_path),
    ]) == 0
    assert calls[0][calls[0].index("--light") + 1] == str(
        (newer / "light_amp.csv").resolve()
    )
    assert calls[0][calls[0].index("--session-root") + 1] == str(
        (tmp_path / "psychopy-ssvep" / "sessions").resolve()
    )
