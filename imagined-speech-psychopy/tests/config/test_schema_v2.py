from pathlib import Path

import pytest
import yaml

from imagined_speech.config import ConfigurationError, load_experiment


def test_live_schema_one_has_targeted_migration_error(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="presentation.psychopy"):
        load_experiment(path)
