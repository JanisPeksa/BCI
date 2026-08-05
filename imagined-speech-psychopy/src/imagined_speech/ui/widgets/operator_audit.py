"""Operator command audit projection."""

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from imagined_speech.ipc.messages import OperatorStatePayload


class OperatorAuditWidget(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Seq", "Command", "Result", "Reason"])
        self._last_sequence = -1

    def clear(self) -> None:  # type: ignore[override]
        self._last_sequence = -1
        self.setRowCount(0)

    def render(self, state: OperatorStatePayload) -> None:
        records = state.operator_records
        newest = records[-1].sequence_number if records else 0
        if newest == self._last_sequence:
            return
        self._last_sequence = newest
        recent = records[-10:]
        self.setRowCount(len(recent))
        for row, record in enumerate(recent):
            values = (
                str(record.sequence_number),
                record.command.value,
                record.status.value,
                record.reason,
            )
            for column, value in enumerate(values):
                self.setItem(row, column, QTableWidgetItem(value))
