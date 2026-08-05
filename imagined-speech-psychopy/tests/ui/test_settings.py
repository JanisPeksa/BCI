import os
from importlib.resources import files
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from imagined_speech.ui.settings import ExperimenterSettingsStore


def test_new_settings_file_is_seeded_from_committed_ini(tmp_path: Path) -> None:
    target = tmp_path / "operator" / "experimenter_ui.ini"

    store = ExperimenterSettingsStore(target)

    committed = files("imagined_speech").joinpath(
        "resources", "experimenter_ui.ini"
    ).read_bytes()
    assert target.read_bytes() == committed
    assert store.int_value("schema/version") == 2
    assert store.value("workspace/layout_id") == "grid_4"


def test_settings_updates_are_per_user_copy_not_package_resource(
    tmp_path: Path,
) -> None:
    target = tmp_path / "experimenter_ui.ini"
    committed_path = Path(str(files("imagined_speech").joinpath(
        "resources", "experimenter_ui.ini"
    )))
    before = committed_path.read_bytes()
    store = ExperimenterSettingsStore(target)

    store.set_value("setup/participant_id", "P042")
    store.sync()

    restored = ExperimenterSettingsStore(target)
    assert restored.value("setup/participant_id") == "P042"
    assert committed_path.read_bytes() == before
