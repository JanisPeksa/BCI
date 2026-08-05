from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from imagined_speech.cli import default_config_path, main
from imagined_speech.config import (
    ConfigurationError,
    Phase,
    PresentationConfig,
    SubjectWindowMode,
    load_device_profile,
    load_experiment,
    resolve_session_setup,
)
from imagined_speech.planning.preview import format_duration


RESOURCE_ROOT = Path(__file__).parents[2] / "src" / "imagined_speech" / "resources"


@pytest.mark.parametrize(
    ("name", "phase", "duration"),
    [
        ("imagined_only.yaml", Phase.PAUSE, "00:59:40"),
        ("feis_comparable.yaml", Phase.SPEAKING, "00:59:40"),
    ],
)
def test_full_profiles_are_balanced(name: str, phase: Phase, duration: str) -> None:
    resolved = load_experiment(RESOURCE_ROOT / "configs" / name)

    assert len(resolved.config.stimuli) == 16
    assert resolved.config.recorded_trials == 160
    assert resolved.config.trials_per_block == 40
    assert resolved.config.practice_trials == 4
    assert phase in resolved.config.phase_sequence
    assert format_duration(resolved.config.projected_duration_seconds) == duration
    assert len(resolved.device.eeg_channels) == 8


def test_smoke_profile_is_hardware_and_asset_independent() -> None:
    resolved = load_experiment(default_config_path())

    assert resolved.device.backend == "synthetic"
    assert resolved.config.presentation.psychopy.window_mode == SubjectWindowMode.FULL_SCREEN
    assert resolved.config.presentation.audio.enabled is False
    assert all(not assets for assets in resolved.assets.values())
    assert format_duration(resolved.config.projected_duration_seconds) == "00:00:14"


def test_session_setup_resolver_applies_operator_overrides() -> None:
    device_path = RESOURCE_ROOT / "devices" / "brainflow_synthetic.yaml"

    resolved = resolve_session_setup(
        default_config_path(),
        device_profile_path=device_path,
        random_seed=314,
        screen_index=1,
        window_mode=SubjectWindowMode.CENTER,
    )

    assert resolved.device_path == device_path.resolve()
    assert resolved.device.backend == "brainflow_synthetic"
    assert resolved.config.random_seed == 314
    assert resolved.config.presentation.psychopy.screen_index == 1
    assert (
        resolved.config.presentation.psychopy.window_mode
        == SubjectWindowMode.CENTER
    )


@pytest.mark.parametrize("value", [True, False])
def test_legacy_full_screen_configuration_is_rejected(value: bool) -> None:
    with pytest.raises(ValueError, match="full_screen"):
        PresentationConfig.model_validate({"full_screen": value})


@pytest.mark.parametrize(
    ("name", "backend"),
    [
        ("synthetic.yaml", "synthetic"),
        ("brainflow_synthetic.yaml", "brainflow_synthetic"),
        ("replay.yaml", "replay"),
        ("cyton.yaml", "cyton"),
        ("lsl.yaml", "lsl"),
    ],
)
def test_device_profiles_validate(name: str, backend: str) -> None:
    profile = load_device_profile(RESOURCE_ROOT / "devices" / name)

    assert profile.backend == backend
    assert len(profile.eeg_channels) == 8


def test_missing_enabled_asset_is_rejected(tmp_path: Path) -> None:
    source = default_config_path()
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data["device_profile"] = str((RESOURCE_ROOT / "devices" / "synthetic.yaml").resolve())
    data["presentation"]["audio"]["enabled"] = True
    data["stimuli"][0]["audio"] = "missing.wav"
    target = tmp_path / "missing-asset.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="referenced assets do not exist"):
        load_experiment(target)


def test_marker_collisions_are_rejected(tmp_path: Path) -> None:
    source = default_config_path()
    data = deepcopy(yaml.safe_load(source.read_text(encoding="utf-8")))
    data["device_profile"] = str((RESOURCE_ROOT / "devices" / "synthetic.yaml").resolve())
    data["markers"] = {"session_start": 40}
    target = tmp_path / "marker-collision.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="fixed marker codes must be unique"):
        load_experiment(target)


def test_preview_command_uses_smoke_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preview"]) == 0

    output = capsys.readouterr().out
    assert "Imagined Speech Smoke Test" in output
    assert "Subject window mode: FULL_SCREEN" in output
    assert "Projected duration: 00:00:14" in output
    assert "cyton_8ch_synthetic" in output
