"""Command-line entry point for configuration validation and previews."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Sequence

from imagined_speech.config import ConfigurationError, load_experiment
from imagined_speech.engine import ProtocolEngine, RealClock, RunState, VirtualClock
from imagined_speech.plan import compile_session_plan
from imagined_speech.preview import render_preview
from imagined_speech.session import (
    SessionValidationError,
    SessionWriter,
    validate_session,
)
from imagined_speech.simulation import run_real, run_virtual


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "resources" / "configs" / "smoke.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imagined-speech",
        description="Configurable imagined-speech EEG experiment platform",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("validate", "validate an experiment and its referenced resources"),
        ("preview", "show the protocol layout and projected duration"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument(
            "--config",
            type=Path,
            default=default_config_path(),
            help="experiment YAML path (defaults to the bundled smoke profile)",
        )

    simulate = subparsers.add_parser(
        "simulate", help="execute a headless session and write a session package"
    )
    _add_run_arguments(simulate)
    simulate.add_argument(
        "--clock", choices=("virtual", "real"), default="virtual"
    )

    run = subparsers.add_parser("run", help="run the subject-facing Qt display")
    _add_run_arguments(run)
    run.add_argument("--clock", choices=("real", "virtual"), default="real")
    run.add_argument("--windowed", action="store_true", help="do not use full screen")
    run.add_argument("--screen", type=int, help="override the configured screen index")

    validate_package = subparsers.add_parser(
        "validate-session", help="validate and reconstruct a session package"
    )
    validate_package.add_argument("session", type=Path)
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="experiment YAML path (defaults to the bundled smoke profile)",
    )
    parser.add_argument("--participant", default="SIM001")
    parser.add_argument(
        "--output",
        type=Path,
        help="session output root (overrides the configuration)",
    )


def _execute_session(args: argparse.Namespace, *, subject_ui: bool) -> int:
    resolved = load_experiment(args.config)
    plan = compile_session_plan(resolved.config)
    writer = SessionWriter(
        resolved,
        plan,
        participant_id=args.participant,
        output_root=args.output,
    )
    clock = VirtualClock() if args.clock == "virtual" else RealClock()
    engine = ProtocolEngine(
        writer.session_id,
        plan,
        resolved.config,
        clock,
        writer,
    )
    try:
        if subject_ui:
            from imagined_speech.subject_ui import run_subject_window

            exit_code = run_subject_window(
                engine,
                resolved,
                windowed=args.windowed,
                screen_index=args.screen,
            )
        elif args.clock == "virtual":
            run_virtual(engine)
            exit_code = 0
        else:
            run_real(engine)
            exit_code = 0
    except Exception as exc:
        if engine.state not in {RunState.COMPLETED, RunState.ABORTED, RunState.FAILED}:
            engine.fail(str(exc))
        print(f"Session error: {exc}", file=sys.stderr)
        if args.__dict__.get("debug"):
            traceback.print_exc()
        exit_code = 1
    finally:
        writer.finalize_incomplete()

    print(f"Session package: {writer.path}")
    if exit_code == 0:
        report = validate_session(writer.path)
        print(
            f"Session status: {report.status}; {report.event_count} events; "
            f"{report.trial_count} completed trials"
        )
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        parser.print_help()
        return 0

    args = parser.parse_args(arguments)
    if args.command == "validate-session":
        try:
            report = validate_session(args.session)
        except SessionValidationError as exc:
            print(f"Session validation error: {exc}", file=sys.stderr)
            return 2
        print(f"Valid session package: {report.session_path}")
        print(
            f"Status: {report.status}; events: {report.event_count}; "
            f"completed trials: {report.trial_count}; completed phases: {report.phase_count}"
        )
        for warning in report.warnings:
            print(f"Warning: {warning}")
        return 0

    try:
        if args.command == "simulate":
            return _execute_session(args, subject_ui=False)
        if args.command == "run":
            return _execute_session(args, subject_ui=True)
        resolved = load_experiment(args.config)
    except (ConfigurationError, SessionValidationError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        print(f"Valid experiment configuration: {resolved.config_path}")
        print(f"Valid device profile: {resolved.device_path}")
        return 0
    if args.command == "preview":
        print(render_preview(resolved))
        return 0
    parser.error("a command is required")
    return 2
