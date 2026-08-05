"""Persistent PsychoPy renderer for presentation-safe view dictionaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from imagined_speech.displays import (
    native_display_for_window,
    normalize_device_name,
)
from imagined_speech.ipc.messages import SubjectDisplayTargetPayload


class DisplaySelectionError(RuntimeError):
    def __init__(self, message: str, metadata: dict[str, Any]) -> None:
        super().__init__(message)
        self.metadata = {"display": metadata}


@dataclass(frozen=True)
class ResolvedPsychopyDisplay:
    index: int
    device_name: str | None
    geometry: tuple[int, int, int, int]
    screen: Any

    def metadata(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "device_name": self.device_name,
            "geometry": list(self.geometry),
        }


def _screen_device_name(screen: Any) -> str | None:
    getter = getattr(screen, "get_device_name", None)
    if getter is None:
        return None
    try:
        return str(getter())
    except Exception:
        return None


def _screen_geometry(screen: Any) -> tuple[int, int, int, int]:
    return (
        int(screen.x),
        int(screen.y),
        int(screen.width),
        int(screen.height),
    )


def _available_display_metadata(screens: list[Any]) -> list[dict[str, Any]]:
    return [
        ResolvedPsychopyDisplay(
            index=index,
            device_name=_screen_device_name(screen),
            geometry=_screen_geometry(screen),
            screen=screen,
        ).metadata()
        for index, screen in enumerate(screens)
    ]


def resolve_psychopy_display(
    screens: list[Any],
    configured_index: int,
    display_target: dict[str, Any] | SubjectDisplayTargetPayload | None,
) -> ResolvedPsychopyDisplay:
    available = _available_display_metadata(screens)
    requested = (
        SubjectDisplayTargetPayload.model_validate(display_target)
        if display_target is not None
        else None
    )
    selected_index: int | None = None

    if requested is not None and requested.device_name:
        requested_name = normalize_device_name(requested.device_name)
        selected_index = next(
            (
                index
                for index, screen in enumerate(screens)
                if normalize_device_name(_screen_device_name(screen)) == requested_name
            ),
            None,
        )
        if selected_index is None:
            raise DisplaySelectionError(
                f"requested subject display {requested.device_name!r} is unavailable; "
                f"available displays: {available}",
                {
                    "requested": requested.model_dump(mode="json"),
                    "available": available,
                    "verified": False,
                },
            )
    elif requested is not None:
        geometry_matches = [
            index
            for index, screen in enumerate(screens)
            if _screen_geometry(screen) == requested.geometry
        ]
        if len(geometry_matches) == 1:
            selected_index = geometry_matches[0]
        elif requested.psychopy_index < len(screens):
            selected_index = requested.psychopy_index
    elif 0 <= configured_index < len(screens):
        selected_index = configured_index

    if selected_index is None:
        requested_value: Any = (
            requested.model_dump(mode="json")
            if requested is not None
            else {"screen_index": configured_index}
        )
        raise DisplaySelectionError(
            f"requested subject display {requested_value!r} is unavailable; "
            f"available displays: {available}",
            {
                "requested": requested_value,
                "available": available,
                "verified": False,
            },
        )

    screen = screens[selected_index]
    return ResolvedPsychopyDisplay(
        index=selected_index,
        device_name=_screen_device_name(screen),
        geometry=_screen_geometry(screen),
        screen=screen,
    )


def _window_location(window: Any) -> list[int] | None:
    handle = getattr(window, "winHandle", None)
    getter = getattr(handle, "get_location", None)
    if getter is None:
        return None
    try:
        return [int(value) for value in getter()]
    except Exception:
        return None


def create_window(
    config: dict,
    display_target: dict[str, Any] | SubjectDisplayTargetPayload | None = None,
):
    import pyglet
    from psychopy import monitors, visual

    screens = list(pyglet.canvas.get_display().get_screens())
    resolved_display = resolve_psychopy_display(
        screens,
        int(config["screen_index"]),
        display_target,
    )

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
    requested_size = config.get("window_size_px")
    if full_screen:
        size = list(resolved_display.geometry[2:])
        size_clamped = False
    else:
        requested_width, requested_height = requested_size or [1024, 720]
        size = [
            min(int(requested_width), resolved_display.geometry[2]),
            min(int(requested_height), resolved_display.geometry[3]),
        ]
        size_clamped = size != [int(requested_width), int(requested_height)]
    position = None
    if not full_screen and mode == "TOP_LEFT":
        position = [0, 0]
    window = visual.Window(
        size=size,
        fullscr=full_screen,
        screen=resolved_display.index,
        pos=position,
        units="pix",
        monitor=monitor,
        allowGUI=not full_screen,
        useRetina=bool(config["use_retina"]),
        checkTiming=bool(config["check_timing"]),
        waitBlanking=bool(config["wait_blanking"]),
        color="#0b0d12",
    )
    actual_display = native_display_for_window(
        int(getattr(window, "_hw_handle", 0) or 0)
    )
    actual_metadata = (
        {
            "index": actual_display.index,
            "device_name": actual_display.device_name,
            "geometry": list(actual_display.geometry),
        }
        if actual_display is not None
        else None
    )
    verified = actual_display is None
    if resolved_display.device_name is not None:
        verified = (
            actual_display is not None
            and normalize_device_name(actual_display.device_name)
            == normalize_device_name(resolved_display.device_name)
        )
    display_metadata = {
        "requested": (
            SubjectDisplayTargetPayload.model_validate(display_target).model_dump(
                mode="json"
            )
            if display_target is not None
            else {"screen_index": int(config["screen_index"])}
        ),
        "resolved": resolved_display.metadata(),
        "actual": actual_metadata,
        "window": {
            "mode": mode,
            "full_screen": full_screen,
            "position": _window_location(window),
            "size": [int(value) for value in window.size],
            "requested_size": requested_size,
            "size_clamped": size_clamped,
        },
        "verified": verified,
    }
    if resolved_display.device_name is not None and not verified:
        window.close()
        raise DisplaySelectionError(
            "PsychoPy window opened on a different display than requested: "
            f"requested {resolved_display.device_name!r}, actual {actual_metadata!r}",
            display_metadata,
        )
    window.mouseVisible = not bool(config["hide_cursor"])
    window._imagined_speech_monitor_metadata = {
        "requested_monitor_name": requested_monitor,
        "resolved_monitor_name": monitor_name,
        "monitor_profile_found": requested_monitor in available_monitors,
        "display": display_metadata,
    }
    return window


def _set_text_if_changed(stimulus, text: str) -> None:
    """Avoid rebuilding PsychoPy glyph geometry when text is unchanged."""
    if stimulus.text != text:
        stimulus.text = text


class PsychopyRenderer:
    def __init__(
        self,
        window,
        presentation: dict,
        assets: dict[str, dict[str, str]],
        presentation_assets: dict[str, str] | None = None,
    ) -> None:
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
        self.fixation = visual.TextStim(
            window,
            text="+",
            height=style["headline_height_px"],
            color=style["primary_color"],
            font=style["font"],
            pos=(0, 0),
        )
        self.minimal_text = visual.TextStim(
            window,
            text="",
            height=style["headline_height_px"],
            color=style["primary_color"],
            font=style["font"],
            pos=(0, 0),
        )
        speaking_image = (presentation_assets or {}).get("speaking_image")
        if speaking_image:
            self.speaking_mouth = visual.ImageStim(
                window,
                image=str(Path(speaking_image)),
                interpolate=bool(psycho["assets"]["interpolate"]),
            )
            width, height = (float(value) for value in self.speaking_mouth.size)
            scale = min(
                1.0,
                float(psycho["assets"]["max_width_px"]) / width,
                float(psycho["assets"]["max_height_px"]) / height,
            )
            self.speaking_mouth.size = (width * scale, height * scale)
        else:
            self.speaking_mouth = visual.ShapeStim(
                window,
                vertices=(
                    (-140, 0),
                    (-70, 48),
                    (0, 58),
                    (70, 48),
                    (140, 0),
                    (70, -48),
                    (0, -58),
                    (-70, -48),
                ),
                closeShape=True,
                lineColor=style["primary_color"],
                fillColor=None,
                lineWidth=6,
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
        if self.presentation.get("subject_style") == "minimal_phoneme":
            screen = view.get("screen")
            if screen == "fixation":
                self.fixation.draw()
            elif screen == "speaking":
                self.speaking_mouth.draw()
            elif screen in {"stimulus", "rest"}:
                _set_text_if_changed(
                    self.minimal_text,
                    str(view.get("headline", "")),
                )
                self.minimal_text.draw()
            if screen != "post_trial":
                self._draw_countdown(remaining_seconds)
            if screen in {
                "fixation",
                "speaking",
                "stimulus",
                "rest",
                "thinking",
                "post_trial",
            }:
                return
        if view.get("screen") == "fixation":
            self.fixation.draw()
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
        self._draw_countdown(remaining_seconds)

    def _draw_countdown(self, remaining_seconds: float | None) -> None:
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
