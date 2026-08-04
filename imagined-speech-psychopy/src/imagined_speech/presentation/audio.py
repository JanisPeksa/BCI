"""PTB-backed audio preloading and flip-synchronized playback."""

from __future__ import annotations

from pathlib import Path


class AudioScheduler:
    def __init__(self, enabled: bool, volume: float, latency_mode: int) -> None:
        self.enabled = enabled
        self.volume = volume
        self.latency_mode = latency_mode
        self._sounds: dict[str, object] = {}

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

    def schedule(self, stimulus_id: str | None, window) -> tuple[float | None, bool]:
        if not self.enabled or stimulus_id is None:
            return None, False
        value = self._sounds.get(stimulus_id)
        if value is None:
            return None, False
        when = float(window.getFutureFlipTime(clock="ptb"))
        value.play(when=when)
        return when, True

    def stop_all(self) -> None:
        for value in self._sounds.values():
            value.stop()

