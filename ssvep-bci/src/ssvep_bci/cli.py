from __future__ import annotations

import argparse
import json
import sys

from ssvep_bci.config.loader import ConfigurationError, load_experiment
from ssvep_bci.planning.compiler import compile_session_plan
from ssvep_bci.recording.session import validate_session


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssvep-bci")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate configuration")
    validate.add_argument("--config")
    run = sub.add_parser("run", help="run the subject application")
    run.add_argument("--config")
    run.add_argument("--participant", required=True)
    run.add_argument("--session-label")
    run.add_argument("--windowed", action="store_true")
    session = sub.add_parser("validate-session", help="validate a recorded session")
    session.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-session":
            print(json.dumps(validate_session(args.path), indent=2))
            return 0
        resolved = load_experiment(args.config)
        if args.command == "validate":
            plan = compile_session_plan(resolved.config)
            print(f"Configuration valid: {resolved.config.title}")
            print(f"Device: {resolved.device.profile_id} ({resolved.device.backend})")
            print(f"Plan: {plan.trial_count} trial(s), {plan.duration_seconds:g} seconds")
            return 0
        from ssvep_bci.ui.application import run_application
        return run_application(
            resolved,
            args.participant,
            args.session_label,
            windowed=args.windowed,
        )
    except (ConfigurationError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
