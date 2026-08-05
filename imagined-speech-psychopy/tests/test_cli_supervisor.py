from argparse import Namespace
from pathlib import Path

from imagined_speech import cli


class _Process:
    def __init__(self, exit_code: int | None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


def test_run_supervisor_does_not_launch_subject_process(monkeypatch) -> None:
    backend = _Process(None)
    operator = _Process(0)
    launched: list[list[str]] = []
    stopped: list[object] = []

    monkeypatch.setattr(cli, "_start_backend", lambda: (backend, 43210))

    def launch(arguments, **_kwargs):  # type: ignore[no-untyped-def]
        launched.append(list(arguments))
        return operator

    monkeypatch.setattr(cli.subprocess, "Popen", launch)
    monkeypatch.setattr(cli, "_stop_children", stopped.extend)

    result = cli._run_supervisor(Namespace(
        config=Path("experiment.yaml"),
        participant="P001",
        session_label=None,
        output=None,
    ))

    assert result == 0
    assert len(launched) == 1
    assert "_operator-client" in launched[0]
    assert "_subject-client" not in launched[0]
    assert stopped == [operator, backend]
