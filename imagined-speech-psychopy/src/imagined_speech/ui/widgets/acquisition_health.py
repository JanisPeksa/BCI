"""Acquisition and storage health projection."""

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from imagined_speech.ipc.messages import OperatorStatePayload


class AcquisitionHealthWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.label = QLabel("-")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def clear(self) -> None:
        self.label.setText("-")

    def render(self, state: OperatorStatePayload) -> None:
        value = state.acquisition
        self.label.setText(
            f"Latest health: {value.last_health_kind} "
            f"({value.last_health_severity})\n"
            f"Dropped batches/samples: {value.dropped_batches}/"
            f"{value.dropped_samples}; timestamp/sequence gaps: "
            f"{value.timestamp_discontinuities}; read/write errors: "
            f"{value.read_errors}/{value.write_errors}\n"
            f"Raw file: {value.raw_file_size_bytes / 1_048_576:.2f} MiB; "
            f"free storage: {value.free_storage_bytes / 1_073_741_824:.1f} GiB"
        )
