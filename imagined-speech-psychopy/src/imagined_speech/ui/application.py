"""PyQt6 experimenter application launcher."""

from __future__ import annotations

from pathlib import Path

from imagined_speech.ui.experimenter_window import format_engine_diagnostic

__all__ = ["format_engine_diagnostic", "run_operator_application"]


def run_operator_application(
    host: str,
    port: int,
    config_path: Path,
    participant: str,
    session_label: str | None = None,
    output_root: Path | None = None,
) -> int:
    from PyQt6.QtWidgets import QApplication
    from imagined_speech.ui.experimenter_window import ExperimenterWindow

    app = QApplication.instance() or QApplication([])
    window = ExperimenterWindow(
        host,
        port,
        config_path,
        participant,
        session_label,
        output_root,
    )
    window.show()
    return app.exec()
