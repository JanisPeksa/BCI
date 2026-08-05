"""Recent protocol marker projection."""

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from imagined_speech.ipc.messages import OperatorStatePayload


class RecentMarkersWidget(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Seq", "Event", "Code", "Time"])
        self._last_sequence = -1

    def clear(self) -> None:  # type: ignore[override]
        self._last_sequence = -1
        self.setRowCount(0)

    def render(self, state: OperatorStatePayload) -> None:
        events = state.events
        newest = events[-1].sequence_number if events else 0
        if newest == self._last_sequence:
            return
        self._last_sequence = newest
        recent = events[-12:]
        self.setRowCount(len(recent))
        for row, event in enumerate(recent):
            values = (
                str(event.sequence_number),
                event.event_type.value,
                str(event.marker_code),
                f"{event.monotonic_seconds:.3f}",
            )
            for column, value in enumerate(values):
                self.setItem(row, column, QTableWidgetItem(value))
