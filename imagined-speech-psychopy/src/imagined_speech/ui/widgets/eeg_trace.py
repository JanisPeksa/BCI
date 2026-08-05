"""Live multichannel EEG trace projection."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from imagined_speech.ipc.messages import OperatorStatePayload


class EEGTraceWidget(QWidget):
    COLORS = (
        "#5cc8ff", "#ff8a65", "#81c784", "#ce93d8",
        "#ffd54f", "#4dd0e1", "#f48fb1", "#aed581",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(140, 90)
        self._samples: tuple[tuple[float, ...], ...] = ()
        self._indexes: tuple[int, ...] = ()
        self._labels: tuple[str, ...] = ()

    def clear(self) -> None:
        self._samples = ()
        self._indexes = ()
        self._labels = ()
        self.update()

    def render(self, state: OperatorStatePayload) -> None:
        acquisition = state.acquisition
        self._samples = acquisition.recent_samples
        self._indexes = acquisition.eeg_channel_indexes
        self._labels = acquisition.eeg_channel_labels
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        if not self._samples or not self._indexes:
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for EEG samples"
            )
            return
        left, right = 48, max(49, self.width() - 8)
        channel_height = self.height() / len(self._indexes)
        stride = max(1, len(self._samples) // max(1, right - left))
        sampled = self._samples[::stride]
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
                row[source_index]
                for row in sampled
                if source_index < len(row)
            ]
            if len(values) < 2:
                continue
            center_value = sum(values) / len(values)
            peak = max(max(abs(value - center_value) for value in values), 1.0)
            scale = channel_height * 0.38 / peak
            path = QPainterPath()
            for index, value in enumerate(values):
                x = left + index * (right - left) / max(1, len(values) - 1)
                y = center - (value - center_value) * scale
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(color, 1.2))
            painter.drawPath(path)
