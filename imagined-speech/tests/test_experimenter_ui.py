import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from imagined_speech.cli import default_config_path
from imagined_speech.experimenter_ui import ExperimenterWindow


def test_experimenter_setup_loads_preview_without_starting_session() -> None:
    app = QApplication.instance() or QApplication([])
    window = ExperimenterWindow(default_config_path(), participant="UI001")
    window.timer.stop()

    assert window.runtime is None
    assert window.start_button.isEnabled()
    assert "Projected duration" in window.preview_text.toPlainText()
    assert "F3" in window.montage_label.text()
    assert window.participant_edit.text() == "UI001"

    window.close()
    app.processEvents()
