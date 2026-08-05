import wave
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from imagined_speech.cli import default_config_path, main
from imagined_speech.config import (
    ConfigurationError,
    Phase,
    PresentationConfig,
    SubjectPresentationStyle,
    SubjectWindowMode,
    load_device_profile,
    load_experiment,
    resolve_session_setup,
)
from imagined_speech.planning.preview import format_duration


RESOURCE_ROOT = Path(__file__).parents[2] / "src" / "imagined_speech" / "resources"


def test_cyton_four_phoneme_profile_is_short_and_uses_five_repeat_audio() -> None:
    resolved = load_experiment(
        RESOURCE_ROOT / "configs" / "cyton-four-phoneme.yaml"
    )

    assert resolved.device.backend == "cyton"
    assert resolved.device.sampling_rate_hz == 250
    assert [stimulus.id for stimulus in resolved.config.stimuli] == ["p", "m", "i", "u"]
    assert resolved.config.phase_sequence == (
        Phase.FIXATION,
        Phase.STIMULUS,
        Phase.FIXATION,
        Phase.THINKING,
        Phase.FIXATION,
        Phase.SPEAKING,
        Phase.REST,
    )
    assert resolved.config.phases[Phase.FIXATION].duration_seconds == 1
    assert resolved.config.protocol.post_trial_seconds == 1
    assert resolved.config.recorded_trials == 8
    assert resolved.config.trials_per_block == 4
    assert resolved.config.practice_trials == 4
    assert format_duration(resolved.config.projected_duration_seconds) == "00:05:03"
    assert (
        resolved.config.presentation.subject_style
        == SubjectPresentationStyle.MINIMAL_PHONEME
    )
    assert resolved.config.presentation.show_countdown is True
    assert resolved.presentation_assets["speaking_image"] == (
        RESOURCE_ROOT / "images" / "mouth.png"
    ).resolve()
    assert resolved.config.presentation.audio.enabled is True
    assert resolved.config.presentation.audio.require_all_stimuli is True

    for stimulus_assets in resolved.assets.values():
        audio_path = stimulus_assets["audio"]
        with wave.open(str(audio_path), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 44_100
            assert recording.getnframes() == 5 * 44_100


def test_smoke_profile_is_hardware_and_asset_independent() -> None:
    resolved = load_experiment(default_config_path())

    assert resolved.device.backend == "synthetic"
    assert resolved.config.presentation.psychopy.window_mode == SubjectWindowMode.FULL_SCREEN
    assert resolved.config.presentation.audio.enabled is False
    assert all(not assets for assets in resolved.assets.values())
    assert resolved.presentation_assets == {}
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


def test_previous_position_is_rejected_with_psychopy_migration_guidance() -> None:
    data = load_experiment(default_config_path()).config.presentation.psychopy.model_dump()
    data["window_mode"] = "PREVIOUS_POSITION"

    with pytest.raises(ValueError, match="use CENTER instead"):
        type(load_experiment(default_config_path()).config.presentation.psychopy).model_validate(
            data
        )


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


def test_protocol_sequence_is_configured_and_allows_repeated_phases(
    tmp_path: Path,
) -> None:
    data = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    data["device_profile"] = str(
        (RESOURCE_ROOT / "devices" / "synthetic.yaml").resolve()
    )
    data["protocol"]["sequence"] = ["fixation", "stimulus", "fixation"]
    data["phases"] = {
        "fixation": {"duration_seconds": 1, "instruction": "Fixate"},
        "stimulus": {"duration_seconds": 1, "instruction": "Observe"},
    }
    target = tmp_path / "configured-sequence.yaml"
    target.write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )

    resolved = load_experiment(target)

    assert resolved.config.phase_sequence == (
        Phase.FIXATION,
        Phase.STIMULUS,
        Phase.FIXATION,
    )


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
