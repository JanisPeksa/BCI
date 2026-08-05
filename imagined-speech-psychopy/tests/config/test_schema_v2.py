from pathlib import Path

import pytest
import yaml

from imagined_speech.config import ConfigurationError, load_experiment


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_live_schema_has_targeted_migration_error(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({"schema_version": version}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="schema_version: 3"):
        load_experiment(path)
