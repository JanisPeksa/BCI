"""Per-channel reception summary projection."""

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from imagined_speech.ipc.messages import OperatorStatePayload


class ChannelReceptionWidget(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Channel", "Status", "Latest", "Range"])

    def clear(self) -> None:  # type: ignore[override]
        self.setRowCount(0)

    def render(self, state: OperatorStatePayload) -> None:
        acquisition = state.acquisition
        indexes = acquisition.eeg_channel_indexes
        labels = acquisition.eeg_channel_labels
        samples = acquisition.recent_samples[-250:]
        self.setRowCount(len(indexes))
        for row, source_index in enumerate(indexes):
            label = labels[row] if row < len(labels) else str(source_index)
            values = [
                sample[source_index]
                for sample in samples
                if source_index < len(sample)
            ]
            if values:
                span = max(values) - min(values)
                status = "receiving" if span > 1e-9 else "flat"
                latest, range_text = f"{values[-1]:.2f}", f"{span:.2f}"
            else:
                status, latest, range_text = "no data", "-", "-"
            for column, value in enumerate((label, status, latest, range_text)):
                self.setItem(row, column, QTableWidgetItem(value))
