#!/usr/bin/env python3
"""Portable coordinator for SSVEP monitor frequency validation.

This command does not assume a particular virtual-environment layout. The
``run`` subcommand uses the Python executable that launched this command by
default, or an explicitly supplied ``--python`` executable.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


APPLICATIONS = {
    "ssvep-bci": "ssvep_bci",
    "psychopy-ssvep": "psychopy_ssvep",
}


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _default_session_root(workspace: Path, application: str) -> Path:
    return workspace / application / "sessions"


def _latest_measurement(root: Path) -> Path:
    if not root.is_dir():
        raise ValueError(f"measurement root does not exist: {root}")
    candidates = [
        child
        for child in root.iterdir()
        if child.is_dir() and (child / "light_amp.csv").is_file()
    ]
    if not candidates:
        raise ValueError(
            f"no measurement folder containing light_amp.csv was found under {root}"
        )
    return max(
        candidates,
        key=lambda child: ((child / "light_amp.csv").stat().st_mtime_ns, child.name),
    )


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        default=".",
        help="workspace used for default measurements and session paths",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssvep-frequency-validation",
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser(
        "record",
        help="record photosensor samples into a new timestamped measurement folder",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_workspace(record)
    record.add_argument("--port", required=True)
    record.add_argument("--baud", type=int, default=115200)
    record.add_argument("--measurement-root")
    record.add_argument("--name", help="measurement folder name")
    record.add_argument("--warmup", type=int, default=100)
    record.add_argument("--batch", type=int, default=7000)
    record.add_argument("--seconds", type=float)

    run = commands.add_parser(
        "run",
        help="launch an application's bundled frequency-validation profile",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_workspace(run)
    run.add_argument("application", choices=sorted(APPLICATIONS))
    run.add_argument("--participant", default="MONITOR_TEST")
    run.add_argument("--session-label", default="frequency-validation")
    run.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable in which the selected application is installed",
    )
    run.add_argument(
        "--config",
        default="frequency-validation",
        help="config path or bundled config name",
    )
    run.add_argument("--session-root")
    run.add_argument("--windowed", action="store_true")

    analyze = commands.add_parser(
        "analyze",
        help="analyze the newest measurement against the newest app session",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_workspace(analyze)
    analyze.add_argument("application", choices=sorted(APPLICATIONS))
    analyze.add_argument("--session-root")
    analyze.add_argument("--measurement-root")
    analyze.add_argument(
        "--measurement",
        help="specific measurement folder or light_amp.csv (default: newest folder)",
    )
    analyze.add_argument("--latency", type=float, default=0.07)
    analyze.add_argument("--tolerance-hz", type=float, default=0.25)
    return parser


def _record(args: argparse.Namespace) -> int:
    workspace = _path(args.workspace)
    measurement_root = (
        _path(args.measurement_root)
        if args.measurement_root
        else workspace / "measurements"
    )
    name = args.name or f"frequency-validation-{datetime.now():%Y%m%d-%H%M%S}"
    output = measurement_root / name
    if output.exists():
        raise ValueError(f"measurement directory already exists: {output}")

    from record_photosensor import main as record_main

    forwarded = [
        "--port", args.port,
        "--baud", str(args.baud),
        "--out", str(output),
        "--warmup", str(args.warmup),
        "--batch", str(args.batch),
    ]
    if args.seconds is not None:
        forwarded.extend(("--seconds", str(args.seconds)))
    print(f"measurement: {output}")
    return record_main(forwarded)


def _run_application(args: argparse.Namespace) -> int:
    workspace = _path(args.workspace)
    session_root = (
        _path(args.session_root)
        if args.session_root
        else _default_session_root(workspace, args.application)
    )
    command = [
        str(Path(args.python).expanduser()),
        "-m", APPLICATIONS[args.application],
        "run",
        "--config", args.config,
        "--participant", args.participant,
        "--session-label", args.session_label,
        "--output-root", str(session_root),
    ]
    if args.windowed:
        command.append("--windowed")
    print(f"application: {args.application}")
    print(f"python: {command[0]}")
    print(f"sessions: {session_root}")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        raise ValueError(f"cannot launch Python executable {command[0]}: {exc}") from exc


def _analyze(args: argparse.Namespace) -> int:
    workspace = _path(args.workspace)
    session_root = (
        _path(args.session_root)
        if args.session_root
        else _default_session_root(workspace, args.application)
    )
    if args.measurement:
        selected = _path(args.measurement)
        light = selected if selected.is_file() else selected / "light_amp.csv"
    else:
        measurement_root = (
            _path(args.measurement_root)
            if args.measurement_root
            else workspace / "measurements"
        )
        selected = _latest_measurement(measurement_root)
        light = selected / "light_amp.csv"
    if not light.is_file():
        raise ValueError(f"photosensor recording was not found: {light}")

    from run_check import main as check_main

    print(f"measurement: {selected}")
    print(f"session root: {session_root}")
    return check_main([
        "--latest",
        "--session-root", str(session_root),
        "--light", str(light),
        "--latency", str(args.latency),
        "--tolerance-hz", str(args.tolerance_hz),
    ])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "record":
            return _record(args)
        if args.command == "run":
            return _run_application(args)
        return _analyze(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
