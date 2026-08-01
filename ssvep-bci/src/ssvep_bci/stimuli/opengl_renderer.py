from __future__ import annotations

import math

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ssvep_bci.runtime.clock import Clock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.view_state import ViewState


class StimulusRenderer(QOpenGLWidget):
    command_emitted = Signal(object)

    def __init__(self, clock: Clock, background_color: str, parent=None) -> None:
        super().__init__(parent)
        self.clock = clock
        self.background = QColor(background_color)
        self._view: ViewState | None = None
        self._requested_flashing = False
        self._phase_anchor = 0.0
        self._pending_ack: FrameKind | None = None
        self._last_painted_on = False
        self.frameSwapped.connect(self._frame_swapped)

    def set_view_state(self, view: ViewState) -> None:
        previous = self._view
        previous_flashing = self._requested_flashing
        self._view = view
        self._requested_flashing = bool(view.scene and view.scene.has_flashing_nodes)
        if self._requested_flashing and not previous_flashing:
            self._phase_anchor = self.clock.monotonic()
            self._pending_ack = FrameKind.ONSET
            self.update()
        elif not self._requested_flashing and previous_flashing:
            self._pending_ack = FrameKind.OFFSET
            self.update()
        elif previous is None or previous.step_id != view.step_id:
            self.update()

    def paintGL(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background)
        self._last_painted_on = False
        view = self._view
        if view is not None and view.scene is not None and view.scene.has_visible_nodes:
            elapsed = max(0.0, self.clock.monotonic() - self._phase_anchor)
            for node in sorted(view.scene.nodes, key=lambda item: item.z_order):
                if not node.visible_requested:
                    continue
                stimulus = node.stimulus
                if node.flashing_requested:
                    phase = (
                        elapsed * stimulus.frequency_hz
                        + stimulus.phase_offset_radians / (2 * math.pi)
                    ) % 1.0
                    is_on = phase < stimulus.duty_cycle
                    self._last_painted_on = self._last_painted_on or is_on
                else:
                    is_on = False
                visual = stimulus.visual
                if node.placement_override is not None:
                    rect = node.placement_override.resolve_rect(
                        self.width(), self.height()
                    )
                else:
                    rect = visual.resolve_rect(self.width(), self.height())
                color = QColor(visual.on_color if is_on else visual.off_color)
                painter.setPen(color)
                painter.setBrush(color)
                if visual.shape.value == "circle":
                    painter.drawEllipse(*rect)
                else:
                    painter.drawRect(*rect)
                if node.highlighted:
                    painter.setPen(QPen(QColor("#FF3040"), 8))
                    painter.setBrush(QColor(0, 0, 0, 0))
                    if visual.shape.value == "circle":
                        painter.drawEllipse(*rect)
                    else:
                        painter.drawRect(*rect)
        painter.end()

    @Slot()
    def _frame_swapped(self) -> None:
        view = self._view
        if view is None or view.presentation_id is None:
            return
        if self._pending_ack == FrameKind.ONSET and self._last_painted_on:
            now = self.clock.monotonic()
            self._phase_anchor = now
            self.command_emitted.emit(PresentationFrameAcknowledged(
                presentation_id=view.presentation_id,
                kind=FrameKind.ONSET,
                monotonic_timestamp=now,
                wall_clock_timestamp_utc=self.clock.wall_time_utc(),
            ))
            self._pending_ack = None
        elif self._pending_ack == FrameKind.OFFSET and not self._last_painted_on:
            self.command_emitted.emit(PresentationFrameAcknowledged(
                presentation_id=view.presentation_id,
                kind=FrameKind.OFFSET,
                monotonic_timestamp=self.clock.monotonic(),
                wall_clock_timestamp_utc=self.clock.wall_time_utc(),
            ))
            self._pending_ack = None
        if self._requested_flashing:
            self.update()
