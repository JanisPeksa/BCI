"""Live multichannel EEG sweep projection."""

from collections import deque

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from imagined_speech.ipc.messages import OperatorStatePayload


class EEGTraceWidget(QWidget):
    SWEEP_CAPACITY = 750
    COLORS = (
        "#5cc8ff", "#ff8a65", "#81c784", "#ce93d8",
        "#ffd54f", "#4dd0e1", "#f48fb1", "#aed581",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(140, 90)
        self._samples: list[tuple[float, ...] | None] = self._empty_sweep()
        self._pending_samples: deque[tuple[float, ...]] = deque()
        self._write_index = 0
        self._last_sample_count: int | None = None
        self._session_id: str | None = None
        self._sampling_rate_hz: float | None = None
        self._indexes: tuple[int, ...] = ()
        self._labels: tuple[str, ...] = ()

        self._sweep_timer = QTimer(self)
        self._sweep_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sweep_timer.timeout.connect(self._advance_sweep)

    def _empty_sweep(self) -> list[tuple[float, ...] | None]:
        return [None] * self.SWEEP_CAPACITY

    def clear(self) -> None:
        self._sweep_timer.stop()
        self._samples = self._empty_sweep()
        self._pending_samples.clear()
        self._write_index = 0
        self._last_sample_count = None
        self._session_id = None
        self._sampling_rate_hz = None
        self._indexes = ()
        self._labels = ()
        self.update()

    def render(self, state: OperatorStatePayload) -> None:
        acquisition = state.acquisition
        sample_count = acquisition.sample_count
        recent_samples = acquisition.recent_samples
        indexes = acquisition.eeg_channel_indexes
        labels = acquisition.eeg_channel_labels
        sampling_rate_hz = acquisition.sampling_rate_hz

        requires_resync = (
            self._last_sample_count is None
            or self._session_id != state.session_id
            or self._indexes != indexes
            or self._labels != labels
            or self._sampling_rate_hz != sampling_rate_hz
            or sample_count < self._last_sample_count
        )
        if requires_resync:
            self._resynchronize(
                state.session_id,
                sample_count,
                recent_samples,
                indexes,
                labels,
                sampling_rate_hz,
            )
            return

        new_sample_count = sample_count - self._last_sample_count
        if new_sample_count == 0:
            return

        recent_start = sample_count - len(recent_samples)
        if self._last_sample_count < recent_start:
            self._resynchronize(
                state.session_id,
                sample_count,
                recent_samples,
                indexes,
                labels,
                sampling_rate_hz,
            )
            return

        new_samples = recent_samples[self._last_sample_count - recent_start:]
        if len(new_samples) != new_sample_count:
            self._resynchronize(
                state.session_id,
                sample_count,
                recent_samples,
                indexes,
                labels,
                sampling_rate_hz,
            )
            return

        self._last_sample_count = sample_count
        self._pending_samples.extend(new_samples)
        if len(self._pending_samples) > self.SWEEP_CAPACITY:
            self._resynchronize(
                state.session_id,
                sample_count,
                recent_samples,
                indexes,
                labels,
                sampling_rate_hz,
            )
            return
        if self._pending_samples and not self._sweep_timer.isActive():
            self._sweep_timer.start()

    def _resynchronize(
        self,
        session_id: str,
        sample_count: int,
        recent_samples: tuple[tuple[float, ...], ...],
        indexes: tuple[int, ...],
        labels: tuple[str, ...],
        sampling_rate_hz: float,
    ) -> None:
        self._sweep_timer.stop()
        self._pending_samples.clear()
        self._samples = self._empty_sweep()
        first_sample_index = sample_count - len(recent_samples)
        for offset, sample in enumerate(recent_samples):
            slot = (first_sample_index + offset) % self.SWEEP_CAPACITY
            self._samples[slot] = sample

        self._write_index = sample_count % self.SWEEP_CAPACITY
        if recent_samples:
            self._samples[self._write_index] = None
        self._last_sample_count = sample_count
        self._session_id = session_id
        self._sampling_rate_hz = sampling_rate_hz
        self._indexes = indexes
        self._labels = labels
        self._sweep_timer.setInterval(max(1, round(1000 / sampling_rate_hz)))
        self.update()

    def _advance_sweep(self) -> None:
        if not self._pending_samples:
            self._sweep_timer.stop()
            return

        self._samples[self._write_index] = self._pending_samples.popleft()
        self._write_index = (self._write_index + 1) % self.SWEEP_CAPACITY
        self._samples[self._write_index] = None
        if not self._pending_samples:
            self._sweep_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        if not any(sample is not None for sample in self._samples) or not self._indexes:
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for EEG samples"
            )
            return

        left, right = 48, max(49, self.width() - 8)
        channel_height = self.height() / len(self._indexes)
        for channel_position, source_index in enumerate(self._indexes):
            center = (channel_position + 0.5) * channel_height
            painter.setPen(QPen(QColor("#263746"), 1))
            painter.drawLine(left, int(center), right, int(center))
            color = QColor(self.COLORS[channel_position % len(self.COLORS)])
            label = (
                self._labels[channel_position]
                if channel_position < len(self._labels)
                else str(source_index)
            )
            painter.setPen(color)
            painter.drawText(5, int(center + 4), label)
            values = [
                sample[source_index]
                for sample in self._samples
                if sample is not None and source_index < len(sample)
            ]
            if len(values) < 2:
                continue
            center_value = sum(values) / len(values)
            peak = max(max(abs(value - center_value) for value in values), 1.0)
            scale = channel_height * 0.38 / peak
            path = QPainterPath()
            continuing = False
            for index, sample in enumerate(self._samples):
                if sample is None or source_index >= len(sample):
                    continuing = False
                    continue
                x = left + index * (right - left) / (self.SWEEP_CAPACITY - 1)
                y = center - (sample[source_index] - center_value) * scale
                if continuing:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                    continuing = True
            painter.setPen(QPen(color, 1.2))
            painter.drawPath(path)

        cursor_x = left + self._write_index * (
            right - left
        ) / (self.SWEEP_CAPACITY - 1)
        painter.setPen(QPen(QColor("#eceff1"), 1))
        painter.drawLine(int(cursor_x), 0, int(cursor_x), self.height())
