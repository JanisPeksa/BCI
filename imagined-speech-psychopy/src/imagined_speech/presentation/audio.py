"""PTB-backed audio preloading and flip-synchronized playback."""

from __future__ import annotations

from pathlib import Path


class AudioScheduler:
    def __init__(self, enabled: bool, volume: float, latency_mode: int) -> None:
        self.enabled = enabled
        self.volume = volume
        self.latency_mode = latency_mode
        self._sounds: dict[str, object] = {}
        self._active: object | None = None

    def preload(self, assets: dict[str, dict[str, str]]) -> None:
        if not self.enabled:
            return
        from psychopy import prefs

        prefs.hardware["audioLib"] = ["ptb"]
        prefs.hardware["audioLatencyMode"] = [str(self.latency_mode)]
        from psychopy import sound

        for stimulus_id, values in assets.items():
            path = values.get("audio")
            if path:
                self._sounds[stimulus_id] = sound.Sound(
                    str(Path(path)), volume=self.volume
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

