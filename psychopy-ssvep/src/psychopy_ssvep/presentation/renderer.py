"""PsychoPy scene renderer for SSVEP stimuli.

Consumes the existing ``ViewState`` / ``StimulusScene`` contracts and drives a
persistent ``visual.Window``. Stimulus shapes are created once and mutated per
frame, mirroring ``phychopy-test/scripts/flicker_square.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from psychopy import monitors, visual

from psychopy_ssvep.config.models import (
    PresentationConfig,
    Shape,
    StimulusConfig,
    Waveform,
    WindowMode,
)
from psychopy_ssvep.presentation.waves import (
    hex_to_rgb,
    interpolate_color,
    wave_for_stimulus,
)

if TYPE_CHECKING:
    from psychopy_ssvep.runtime.view_state import ViewState


def _screen_size(screen_index: int) -> list[int]:
    """Resolve the physical resolution of a screen, best-effort."""
    try:
        import pyglet

        screens = pyglet.canvas.get_display().get_screens()
        if 0 <= screen_index < len(screens):
            mode = screens[screen_index].get_mode()
            return [int(mode.width), int(mode.height)]
    except Exception:
        pass
    return [1920, 1080]


def create_window(
    config: PresentationConfig, *, force_windowed: bool = False
) -> visual.Window:
    psycho = config.psychopy
    monitor = monitors.Monitor(psycho.monitor_name)
    if psycho.monitor_size_px is not None:
        monitor.setSizePix(list(psycho.monitor_size_px))
    if psycho.gamma != 1.0:
        monitor.setGamma(psycho.gamma)
    fullscr = config.window_mode == WindowMode.FULL_SCREEN and not force_windowed
    if fullscr:
        size = list(config.window_size_px) if config.window_size_px is not None else (
            list(psycho.monitor_size_px)
            if psycho.monitor_size_px is not None
            else _screen_size(config.screen_index)
        )
    else:
        size = list(config.window_size_px) if config.window_size_px is not None else [1000, 700]
    window = visual.Window(
        size=size,
        units=psycho.units,
        fullscr=fullscr,
        screen=config.screen_index,
        useRetina=psycho.use_retina,
        allowGUI=psycho.allow_gui,
        checkTiming=psycho.check_timing,
        monitor=monitor,
    )
    window.color = config.background_color
    window.mouseVisible = not config.hide_cursor
    return window


class PsychopyRenderer:
    """Owns the psychoPy window objects for every configured stimulus."""

    def __init__(self, window: visual.Window, config: PresentationConfig) -> None:
        self.window = window
        self.config = config
        self.psycho = config.psychopy
        self._shapes: dict[str, visual.ShapeStim] = {}
        self._borders: dict[str, visual.ShapeStim] = {}
        self._waves: dict[str, np.ndarray] = {}
        self._waveform: dict[str, Waveform] = {}
        self._off_hex: dict[str, str] = {}
        self._on_hex: dict[str, str] = {}
        self._off_rgb: dict[str, tuple[float, float, float]] = {}
        self._on_rgb: dict[str, tuple[float, float, float]] = {}
        self._message = visual.TextStim(
            window,
            text="",
            height=self.psycho.message_height_px,
            color=self.psycho.message_color,
        )
        self._requested_flashing = False
        self._pending_ack: str | None = None
        self._wave_index = 0

    def precompute_waves(
        self, stimuli: tuple[StimulusConfig, ...], n_frames: int
    ) -> None:
        refresh = self.psycho.refresh_rate_hz
        gamma = self.psycho.gamma
        for stimulus in stimuli:
            self._waves[stimulus.id] = wave_for_stimulus(
                stimulus, refresh, n_frames, gamma
            )
            self._waveform[stimulus.id] = stimulus.waveform
            self._off_hex[stimulus.id] = stimulus.visual.off_color
            self._on_hex[stimulus.id] = stimulus.visual.on_color
            self._off_rgb[stimulus.id] = hex_to_rgb(stimulus.visual.off_color)
            self._on_rgb[stimulus.id] = hex_to_rgb(stimulus.visual.on_color)

    def set_view_state(self, view: "ViewState") -> None:
        previous_flashing = self._requested_flashing
        self._requested_flashing = bool(view.scene and view.scene.has_flashing_nodes)
        if self._requested_flashing and not previous_flashing:
            self._pending_ack = "onset"
            self._wave_index = 0
        elif not self._requested_flashing and previous_flashing:
            self._pending_ack = "offset"

    @property
    def pending_ack(self) -> str | None:
        return self._pending_ack

    def consume_pending_ack(self) -> str | None:
        value = self._pending_ack
        self._pending_ack = None
        return value

    def advance_wave(self) -> None:
        if self._requested_flashing:
            self._wave_index += 1

    def draw(self, view: "ViewState") -> None:
        window_width, window_height = (int(v) for v in self.window.size)
        scene = view.scene
        if scene is not None:
            for node in sorted(scene.nodes, key=lambda item: item.z_order):
                if not node.visible_requested:
                    continue
                stimulus = node.stimulus
                rect = self._node_rect(node, window_width, window_height)
                shape = self._get_shape(stimulus, rect)
                if node.flashing_requested:
                    wave = self._waves[stimulus.id]
                    value = float(wave[self._wave_index % len(wave)])
                    shape.fillColor = self._frame_color(stimulus.id, value)
                else:
                    shape.fillColor = stimulus.visual.off_color
                shape.draw()
                if node.highlighted:
                    border = self._get_border(stimulus, rect)
                    border.draw()
        if view.message:
            self._message.text = view.message
            self._message.draw()

    def _node_rect(
        self, node, window_width: int, window_height: int
    ) -> tuple[int, int, int, int]:
        """Return ``(left, top, width, height)`` in top-left-origin pixels."""
        stimulus = node.stimulus
        if node.placement_override is not None:
            return node.placement_override.resolve_rect(window_width, window_height)
        return stimulus.visual.resolve_rect(window_width, window_height)

    def _frame_color(self, stimulus_id: str, value: float) -> object:
        if self._waveform[stimulus_id] == Waveform.SQUARE:
            return self._on_hex[stimulus_id] if value > 0 else self._off_hex[stimulus_id]
        blended = interpolate_color(
            self._off_rgb[stimulus_id], self._on_rgb[stimulus_id], value
        )
        return tuple(blended)

    def _get_shape(
        self, stimulus: StimulusConfig, rect: tuple[int, int, int, int]
    ) -> visual.ShapeStim:
        shape = self._shapes.get(stimulus.id)
        if shape is None:
            left, top, width, height = rect
            position = self._center_px(left, top, width, height)
            common = dict(
                units="pix",
                pos=position,
                lineWidth=0,
                interpolate=self.psycho.interpolate,
            )
            if stimulus.visual.shape == Shape.CIRCLE:
                shape = visual.Circle(
                    self.window, radius=width / 2.0, fillColor=stimulus.visual.off_color, **common
                )
            else:
                shape = visual.Rect(
                    self.window, width=width, height=height, fillColor=stimulus.visual.off_color, **common
                )
            self._shapes[stimulus.id] = shape
        else:
            left, top, width, height = rect
            shape.pos = self._center_px(left, top, width, height)
            if hasattr(shape, "size") and not isinstance(shape, visual.Circle):
                shape.size = (width, height)
        return shape

    def _get_border(
        self, stimulus: StimulusConfig, rect: tuple[int, int, int, int]
    ) -> visual.ShapeStim:
        border = self._borders.get(stimulus.id)
        left, top, width, height = rect
        position = self._center_px(left, top, width, height)
        if border is None:
            common = dict(
                units="pix",
                pos=position,
                fillColor=None,
                lineColor=self.psycho.cue_border_color,
                lineWidth=self.psycho.cue_border_width_px,
                interpolate=self.psycho.interpolate,
            )
            if stimulus.visual.shape == Shape.CIRCLE:
                border = visual.Circle(
                    self.window, radius=width / 2.0, **common
                )
            else:
                border = visual.Rect(
                    self.window, width=width, height=height, **common
                )
            self._borders[stimulus.id] = border
        else:
            border.pos = position
            if hasattr(border, "size") and not isinstance(border, visual.Circle):
                border.size = (width, height)
        return border

    def _center_px(
        self, left: int, top: int, width: int, height: int
    ) -> tuple[float, float]:
        """Convert a top-left-origin rect to a PsychoPy center position.

        PsychoPy pixel units place the origin at the window center (y up), so a
        rect at top-left ``(left, top)`` maps to ``(left + w/2 - W/2, H/2 - top - h/2)``.
        A rect centered at 0.5/0.5 therefore lands at ``(0, 0)``.
        """
        window_width, window_height = (int(v) for v in self.window.size)
        return (
            left + width / 2.0 - window_width / 2.0,
            window_height / 2.0 - (top + height / 2.0),
        )
