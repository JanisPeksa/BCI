"""Persistent PsychoPy renderer for presentation-safe view dictionaries."""

from __future__ import annotations

from pathlib import Path


def create_window(config: dict):
    from psychopy import monitors, visual

    requested_monitor = str(config["monitor_name"])
    available_monitors = set(monitors.getAllMonitors())
    monitor_name = requested_monitor
    if monitor_name not in available_monitors and "testMonitor" in available_monitors:
        # PsychoPy logs a warning and creates an implicit temporary calibration for
        # unknown names. Pixel-based presentation does not need that calibration,
        # so use PsychoPy's explicit built-in profile and retain the resolution in
        # session metadata instead of producing a misleading startup warning.
        monitor_name = "testMonitor"
    monitor = monitors.Monitor(monitor_name)
    monitor.setGamma(float(config["gamma"]))
    mode = config["window_mode"]
    full_screen = mode == "FULL_SCREEN"
    size = config.get("window_size_px") or [1920, 1080]
    position = None
    if not full_screen and mode == "TOP_LEFT":
        position = [0, 0]
    window = visual.Window(
        size=size,
        fullscr=full_screen,
        screen=int(config["screen_index"]),
        pos=position,
        units="pix",
        monitor=monitor,
        allowGUI=not full_screen,
        useRetina=bool(config["use_retina"]),
        checkTiming=bool(config["check_timing"]),
        waitBlanking=bool(config["wait_blanking"]),
        color="#0b0d12",
    )
    window.mouseVisible = not bool(config["hide_cursor"])
    window._imagined_speech_monitor_metadata = {
        "requested_monitor_name": requested_monitor,
        "resolved_monitor_name": monitor_name,
        "monitor_profile_found": requested_monitor in available_monitors,
    }
    return window


def _set_text_if_changed(stimulus, text: str) -> None:
    """Avoid rebuilding PsychoPy glyph geometry when text is unchanged."""
    if stimulus.text != text:
        stimulus.text = text


class PsychopyRenderer:
    def __init__(self, window, presentation: dict, assets: dict[str, dict[str, str]]) -> None:
        from psychopy import visual

        self.window = window
        self.presentation = presentation
        psycho = presentation["psychopy"]
        style = psycho["text"]
        self.assets = assets
        self.headline = visual.TextStim(
            window,
            text="",
            height=style["headline_height_px"],
            color=style["primary_color"],
            font=style["font"],
            pos=(0, 120),
            wrapWidth=window.size[0] * 0.85,
        )
        self.instruction = visual.TextStim(
            window,
            text="",
            height=style["instruction_height_px"],
            color=style["primary_color"],
            font=style["font"],
            pos=(0, -180),
            wrapWidth=window.size[0] * 0.85,
        )
        self.progress = visual.TextStim(
            window,
            text="",
            height=style["progress_height_px"],
            color=style["muted_color"],
            font=style["font"],
            pos=(0, window.size[1] / 2 - 50),
        )
        self.countdown = visual.TextStim(
            window,
            text="",
            height=style["countdown_height_px"],
            color=style["accent_color"],
            font=style["font"],
            pos=(0, -260),
        )
        self._images: dict[str, object] = {}
        sizing = psycho["assets"]
        for stimulus_id, values in assets.items():
            image = values.get("image")
            if image:
                stimulus = visual.ImageStim(
                    window,
                    image=str(Path(image)),
                    interpolate=bool(sizing["interpolate"]),
                )
                width, height = (float(value) for value in stimulus.size)
                scale = min(
                    1.0,
                    float(sizing["max_width_px"]) / width,
                    float(sizing["max_height_px"]) / height,
                )
                stimulus.size = (width * scale, height * scale)
                self._images[stimulus_id] = stimulus

    def draw(self, view: dict | None, remaining_seconds: float | None = None) -> None:
        if view is None:
            return
        _set_text_if_changed(self.headline, str(view.get("headline", "")))
        _set_text_if_changed(self.instruction, str(view.get("instruction", "")))
        self.headline.draw()
        stimulus_id = view.get("stimulus_id")
        image = self._images.get(stimulus_id)
        if image is not None and view.get("screen") == "stimulus":
            image.draw()
        self.instruction.draw()
        if self.presentation.get("show_progress") and view.get("trial_number"):
            _set_text_if_changed(
                self.progress,
                f"Trial {view['trial_number']}/{view.get('trial_count') or '?'}",
            )
            self.progress.draw()
        if self.presentation.get("show_countdown") and remaining_seconds is not None:
            _set_text_if_changed(
                self.countdown,
                str(max(0, int(remaining_seconds + 0.999))),
            )
            self.countdown.draw()

    def flip(self) -> float | None:
        return self.window.flip()

    def neutral(self) -> float | None:
        return self.window.flip()
