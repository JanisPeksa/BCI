"""Read-only monitoring projections used inside configurable workspace panes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class EEGTraceView(QWidget):
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
        self.render((), (), ())

    def render(
        self,
        samples: tuple[tuple[float, ...], ...],
        indexes: tuple[int, ...],
        labels: tuple[str, ...],
    ) -> None:
        self._samples, self._indexes, self._labels = samples, indexes, labels
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111820"))
        if not self._samples or not self._indexes:
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for EEG samples")
            return
        left, right = 48, max(49, self.width() - 8)
        channel_height = self.height() / len(self._indexes)
        stride = max(1, len(self._samples) // max(1, right - left))
        sampled = self._samples[::stride]
        for channel_position, source_index in enumerate(self._indexes):
            center = (channel_position + .5) * channel_height
            painter.setPen(QPen(QColor("#263746"), 1))
            painter.drawLine(left, int(center), right, int(center))
            color = QColor(self.COLORS[channel_position % len(self.COLORS)])
            painter.setPen(color)
            label = self._labels[channel_position] if channel_position < len(self._labels) else str(source_index)
            painter.drawText(5, int(center + 4), label)
            values = [row[source_index] for row in sampled if source_index < len(row)]
            if len(values) < 2:
                continue
            center_value = sum(values) / len(values)
            peak = max(max(abs(value - center_value) for value in values), 1.0)
            scale = channel_height * .38 / peak
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


class ChannelReceptionView(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Channel", "Status", "Latest", "Range"])

    def clear(self) -> None:  # type: ignore[override]
        self.setRowCount(0)

    def render(
        self,
        samples: tuple[tuple[float, ...], ...],
        indexes: tuple[int, ...],
        labels: tuple[str, ...],
    ) -> None:
        self.setRowCount(len(indexes))
        recent = samples[-250:]
        for row, (source_index, label) in enumerate(zip(indexes, labels, strict=False)):
            values = [sample[source_index] for sample in recent if source_index < len(sample)]
            if values:
                span = max(values) - min(values)
                status = "receiving" if span > 1e-9 else "flat"
                latest, range_text = f"{values[-1]:.2f}", f"{span:.2f}"
            else:
                status, latest, range_text = "no data", "-", "-"
            for column, value in enumerate((label, status, latest, range_text)):
                self.setItem(row, column, QTableWidgetItem(value))


class RecentMarkersView(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Seq", "Event", "Code", "Time"])
        self._last_count = -1

    def clear(self) -> None:  # type: ignore[override]
        self._last_count = -1
        self.setRowCount(0)

    def render(self, events: list[Any]) -> None:
        if len(events) == self._last_count:
            return
        self._last_count = len(events)
        recent = events[-12:]
        self.setRowCount(len(recent))
        for row, event in enumerate(recent):
            values = (
                str(event.sequence_number), event.event_type.value,
                str(event.marker_code), f"{event.monotonic_seconds:.3f}",
            )
            for column, value in enumerate(values):
                self.setItem(row, column, QTableWidgetItem(value))


class OperatorAuditView(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Seq", "Command", "Result", "Reason"])
        self._last_count = -1

    def clear(self) -> None:  # type: ignore[override]
        self._last_count = -1
        self.setRowCount(0)

    def render(self, records: list[Any]) -> None:
        if len(records) == self._last_count:
            return
        self._last_count = len(records)
        recent = records[-10:]
        self.setRowCount(len(recent))
        for row, record in enumerate(recent):
            values = (
                str(record.sequence_number), record.command.value,
                record.status.value, record.reason,
            )
            for column, value in enumerate(values):
                self.setItem(row, column, QTableWidgetItem(value))


class AcquisitionHealthView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.label = QLabel("-")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def clear(self) -> None:
        self.label.setText("-")

    def render(self, snapshot: Any, session_path: Path) -> None:
        raw_path = session_path / "eeg_raw.csv"
        raw_size = raw_path.stat().st_size if raw_path.is_file() else 0
        free = shutil.disk_usage(session_path).free
        self.label.setText(
            f"Latest health: {snapshot.last_health_kind} ({snapshot.last_health_severity})\n"
            f"Dropped samples: {snapshot.dropped_samples}; timestamp/sequence gaps: "
            f"{snapshot.timestamp_discontinuities}; read/write errors: "
            f"{snapshot.read_errors}/{snapshot.write_errors}\n"
            f"Raw file: {raw_size / 1_048_576:.2f} MiB; "
            f"free storage: {free / 1_073_741_824:.1f} GiB"
        )

