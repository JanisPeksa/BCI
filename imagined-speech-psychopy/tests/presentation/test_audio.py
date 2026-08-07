from __future__ import annotations

import sys
from types import ModuleType

import pytest

from imagined_speech.presentation import audio
from imagined_speech.presentation.audio import AudioScheduler


class _Shaped:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape


class _FakeSlave:
    _imagined_speech_channel_patch_applied = False

    def __init__(
        self,
        stream,
        mode=(1,),
        data=None,
        channels=None,
        select_channels=None,
        volume=None,
    ) -> None:
        self.stream = stream
        self.mode = mode
        self.data = data
        self.channels = channels
        self.select_channels = select_channels
        self.volume = volume


@pytest.fixture
def fake_ptb_audio(monkeypatch: pytest.MonkeyPatch) -> _FakeSlave:
    fake = ModuleType("psychtoolbox.audio")
    fake.Slave = _FakeSlave
    monkeypatch.setitem(sys.modules, "psychtoolbox", ModuleType("psychtoolbox"))
    monkeypatch.setitem(sys.modules, "psychtoolbox.audio", fake)
    return fake.Slave


def test_slave_channel_patch_infers_channels_from_stereo_data(
    fake_ptb_audio: _FakeSlave,
) -> None:
    audio._patch_ptb_slave_channel_count()

    slave = fake_ptb_audio(None, data=_Shaped((100, 2)))

    assert slave.channels == 2
    assert fake_ptb_audio._imagined_speech_channel_patch_applied is True


def test_slave_channel_patch_ignores_1d_data(fake_ptb_audio: _FakeSlave) -> None:
    audio._patch_ptb_slave_channel_count()

    slave = fake_ptb_audio(None, data=_Shaped((100,)))

    assert slave.channels is None


class _FakeSpeaker:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _fake_created.append(self)


_fake_created: list[_FakeSpeaker] = []


class _FakeSound:
    def __init__(self, path: str, volume: float, speaker: object) -> None:
        self.path = path
        self.volume = volume
        self.speaker = speaker


@pytest.fixture
def fake_presentation_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_created.clear()
    fake_ptb_pkg = ModuleType("psychtoolbox")
    fake_ptb_pkg.audio = ModuleType("psychtoolbox.audio")
    fake_ptb_pkg.audio.Slave = _FakeSlave
    monkeypatch.setitem(sys.modules, "psychtoolbox", fake_ptb_pkg)
    monkeypatch.setitem(sys.modules, "psychtoolbox.audio", fake_ptb_pkg.audio)

    fake_psychopy = ModuleType("psychopy")
    fake_psychopy.prefs = ModuleType("psychopy.prefs")
    fake_psychopy.prefs.hardware = {}
    fake_psychopy.sound = ModuleType("psychopy.sound")
    fake_psychopy.sound.Sound = _FakeSound
    fake_psychopy.hardware = ModuleType("psychopy.hardware")
    fake_psychopy.hardware.speaker = ModuleType("psychopy.hardware.speaker")
    fake_psychopy.hardware.speaker.SpeakerDevice = _FakeSpeaker
    monkeypatch.setitem(sys.modules, "psychopy", fake_psychopy)
    monkeypatch.setitem(sys.modules, "psychopy.prefs", fake_psychopy.prefs)
    monkeypatch.setitem(sys.modules, "psychopy.sound", fake_psychopy.sound)
    monkeypatch.setitem(sys.modules, "psychopy.hardware", fake_psychopy.hardware)
    monkeypatch.setitem(
        sys.modules, "psychopy.hardware.speaker", fake_psychopy.hardware.speaker
    )


def test_preload_reuses_a_single_audio_device(
    fake_presentation_deps: None,
) -> None:
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)
    scheduler.preload({
        "p": {"audio": "/tmp/a.wav"},
        "m": {"audio": "/tmp/b.wav"},
        "no-audio": {},
    })

    assert len(_fake_created) == 1
    speaker = _fake_created[0]
    assert speaker.kwargs == {}
    assert scheduler._sounds["p"].speaker is speaker
    assert scheduler._sounds["m"].speaker is speaker
    assert scheduler._sounds["p"].volume == 0.8
    assert "no-audio" not in scheduler._sounds


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("pulse", {"name": "pulse"}),
        (6, {"index": 6}),
        ("6", {"index": 6}),
    ],
)
def test_preload_selects_configured_audio_device(
    fake_presentation_deps: None,
    device: str | int,
    expected: dict[str, str | int],
) -> None:
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3, device=device)
    scheduler.preload({"p": {"audio": "/tmp/a.wav"}})

    assert len(_fake_created) == 1
    assert _fake_created[0].kwargs == expected


class _Sound:
    def __init__(self, events: list[tuple[str, float | None]]) -> None:
        self.events = events

    def play(self, *, when: float) -> None:
        self.events.append(("play", when))

    def stop(self) -> None:
        self.events.append(("stop", None))


class _Window:
    def getFutureFlipTime(self, *, clock: str) -> float:  # noqa: N802 - PsychoPy API
        assert clock == "ptb"
        return 12.5


def test_audio_plays_only_for_stimulus_and_stops_at_next_phase() -> None:
    events: list[tuple[str, float | None]] = []
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)
    scheduler._sounds = {"p": _Sound(events)}
    window = _Window()

    assert scheduler.transition({"screen": "rest", "stimulus_id": "p"}, window) == (
        None,
        False,
    )
    assert scheduler.transition(
        {"screen": "stimulus", "stimulus_id": "p"}, window
    ) == (12.5, True)
    assert events == [("play", 12.5)]

    assert scheduler.transition(
        {"screen": "fixation", "stimulus_id": "p"}, window
    ) == (None, False)
    assert events == [("play", 12.5), ("stop", None)]


def test_restarting_stimulus_stops_previous_playback_first() -> None:
    events: list[tuple[str, float | None]] = []
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)
    scheduler._sounds = {"p": _Sound(events)}
    view = {"screen": "stimulus", "stimulus_id": "p"}
    window = _Window()

    scheduler.transition(view, window)
    scheduler.transition(view, window)

    assert events == [("play", 12.5), ("stop", None), ("play", 12.5)]


def test_disabled_or_missing_audio_never_starts() -> None:
    window = _Window()
    disabled = AudioScheduler(enabled=False, volume=0.8, latency_mode=3)
    enabled = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)

    assert disabled.transition(
        {"screen": "stimulus", "stimulus_id": "p"}, window
    ) == (None, False)
    assert enabled.transition(
        {"screen": "stimulus", "stimulus_id": "missing"}, window
    ) == (None, False)
