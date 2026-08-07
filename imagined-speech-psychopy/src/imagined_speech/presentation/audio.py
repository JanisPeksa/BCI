"""PTB-backed audio preloading and flip-synchronized playback."""

from __future__ import annotations

from pathlib import Path


def _patch_ptb_slave_channel_count() -> None:
    from psychtoolbox import audio as ptb_audio

    if getattr(ptb_audio.Slave, "_imagined_speech_channel_patch_applied", False):
        return
    _original_init = ptb_audio.Slave.__init__

    def _init(
        self,
        stream,
        mode=(1,),
        data=None,
        channels=None,
        select_channels=None,
        volume=None,
    ) -> None:
        if channels is None and data is not None:
            shape = getattr(data, "shape", None)
            if shape is not None and len(shape) == 2:
                channels = int(shape[1])
        _original_init(self, stream, mode, data, channels, select_channels, volume)

    ptb_audio.Slave.__init__ = _init
    ptb_audio.Slave._imagined_speech_channel_patch_applied = True


def _build_speaker(device: str | int | None):
    from psychopy.hardware.speaker import SpeakerDevice

    if device is None:
        return SpeakerDevice()
    if isinstance(device, int):
        return SpeakerDevice(index=device)
    if isinstance(device, str):
        try:
            index = int(device)
        except ValueError:
            return SpeakerDevice(name=device)
        return SpeakerDevice(index=index)
    raise TypeError(f"unsupported audio device specifier: {device!r}")


class AudioScheduler:
    def __init__(
        self,
        enabled: bool,
        volume: float,
        latency_mode: int,
        device: str | int | None = None,
    ) -> None:
        self.enabled = enabled
        self.volume = volume
        self.latency_mode = latency_mode
        self.device = device
        self._sounds: dict[str, object] = {}
        self._active: object | None = None

    def preload(self, assets: dict[str, dict[str, str]]) -> None:
        if not self.enabled:
            return
        from psychopy import prefs

        prefs.hardware["audioLib"] = ["ptb"]
        prefs.hardware["audioLatencyMode"] = [str(self.latency_mode)]
        from psychopy import sound

        _patch_ptb_slave_channel_count()
        speaker = None
        for stimulus_id, values in assets.items():
            path = values.get("audio")
            if path:
                if speaker is None:
                    speaker = _build_speaker(self.device)
                self._sounds[stimulus_id] = sound.Sound(
                    str(Path(path)), volume=self.volume, speaker=speaker
                )

    def transition(self, view: dict | None, window) -> tuple[float | None, bool]:
        """Stop prior playback and play only a newly presented auditory cue."""
        self.stop_all()
        if not self.enabled or view is None or view.get("screen") != "stimulus":
            return None, False
        stimulus_id = view.get("stimulus_id")
        if not isinstance(stimulus_id, str):
            return None, False
        value = self._sounds.get(stimulus_id)
        if value is None:
            return None, False
        when = float(window.getFutureFlipTime(clock="ptb"))
        value.play(when=when)
        self._active = value
        return when, True

    def stop_all(self) -> None:
        if self._active is not None:
            self._active.stop()
            self._active = None

