"""Command-line entry point and local process supervisor."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Sequence

from imagined_speech.config import ConfigurationError, load_experiment
from imagined_speech.acquisition import (
    AcquisitionRecorder,
    create_acquisition_backend,
)
from imagined_speech.acquisition.lsl_publisher import (
    default_lsl_profile_path,
    publish_synthetic_lsl,
)
from imagined_speech.config import load_device_profile
from imagined_speech.runtime.clock import RealClock, VirtualClock
from imagined_speech.runtime.protocol import FrameLockedProtocolEngine, ProtocolEngine, RunState
from imagined_speech.events import CompositeEventSink
from imagined_speech.planning import compile_session_plan
from imagined_speech.planning.preview import render_preview
from imagined_speech.recording import (
    SessionValidationError,
    SessionWriter,
    validate_session,
)
from imagined_speech.runtime.simulation import run_real, run_virtual_presentation


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

    run = subparsers.add_parser(
        "run", help="launch the experimenter workflow and subject display"
    )
    _add_run_arguments(run)

    subject = subparsers.add_parser(
        "run-subject", help="launch a local backend plus PsychoPy without Qt"
    )
    _add_run_arguments(subject)
    subject.add_argument("--clock", choices=("real", "virtual"), default="real")
    subject.add_argument("--windowed", action="store_true", help="do not use full screen")
    subject.add_argument("--screen", type=int, help="override the configured screen index")

    backend = subparsers.add_parser("_backend", help=argparse.SUPPRESS)
    backend.add_argument("--host", default="127.0.0.1")
    backend.add_argument("--port", type=int, default=0)
    subject_client = subparsers.add_parser("_subject-client", help=argparse.SUPPRESS)
    subject_client.add_argument("--host", default="127.0.0.1")
    subject_client.add_argument("--port", type=int, required=True)
    operator_client = subparsers.add_parser("_operator-client", help=argparse.SUPPRESS)
    operator_client.add_argument("--host", default="127.0.0.1")
    operator_client.add_argument("--port", type=int, required=True)
    _add_run_arguments(operator_client)

    validate_package = subparsers.add_parser(
        "validate-session", help="validate and reconstruct a session package"
    )
    validate_package.add_argument("session", type=Path)

    publisher = subparsers.add_parser(
        "publish-lsl-synthetic",
        help="publish a real-time synthetic EEG stream over LSL",
    )
    publisher.add_argument(
        "--device-profile", type=Path, default=default_lsl_profile_path()
    )
    publisher.add_argument("--name", help="override the configured LSL stream name")
    publisher.add_argument(
        "--duration",
        type=float,
        help="stop after this many seconds (otherwise run until Ctrl+C)",
    )
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="experiment YAML path (defaults to the bundled smoke profile)",
    )
    parser.add_argument("--participant", default="SIM001")
    parser.add_argument("--session-label")
    parser.add_argument(
        "--output",
        type=Path,
        help="session output root (overrides the configuration)",
    )


def _execute_session(args: argparse.Namespace) -> int:
    from imagined_speech import __version__

    resolved = load_experiment(args.config)
    if args.clock == "virtual" and resolved.device.backend != "synthetic":
        raise ValueError(
            "virtual protocol time requires the in-process synthetic acquisition profile; "
            "use --clock real for LSL, replay, or Cyton"
        )
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock() if args.clock == "virtual" else RealClock()
    writer = SessionWriter(
        resolved,
        plan,
        participant_id=args.participant,
        output_root=args.output,
        auto_finalize=False,
        session_label=args.session_label,
    )
    writer.record_presentation_metadata({
        "driver": "virtual" if args.clock == "virtual" else "headless-real",
        "passed": True,
        "software_version": __version__,
        "psychopy_version": None,
        "psychopy": resolved.config.presentation.psychopy.model_dump(mode="json"),
        "audio": resolved.config.presentation.audio.model_dump(mode="json"),
        "threshold_outcome": "simulated" if args.clock == "virtual" else "not_measured",
        "clock_calibration_initial": {
            "offset_ns": 0,
            "round_trip_ns": 0,
            "simulated": args.clock == "virtual",
        },
    })
    backend = create_acquisition_backend(resolved, clock)
    acquisition = AcquisitionRecorder(writer.path, backend, clock)
    engine_type = FrameLockedProtocolEngine if args.clock == "virtual" else ProtocolEngine
    engine = engine_type(
        writer.session_id,
        plan,
        resolved.config,
        clock,
        CompositeEventSink(acquisition, writer),
    )
    acquisition_started = False
    try:
        acquisition.start()
        acquisition_started = True
        _recording_delay(resolved.device.pre_roll_seconds, clock, acquisition)
        if args.clock == "virtual":
            assert isinstance(engine, FrameLockedProtocolEngine)
            run_virtual_presentation(engine, writer.record_presentation_timing)
            exit_code = 0
        else:
            run_real(engine)
            exit_code = 0
        _recording_delay(resolved.device.post_roll_seconds, clock, acquisition)
    except Exception as exc:
        if engine.state not in {RunState.COMPLETED, RunState.ABORTED, RunState.FAILED}:
            engine.fail(str(exc))
        print(f"Session error: {exc}", file=sys.stderr)
        if args.__dict__.get("debug"):
            traceback.print_exc()
        exit_code = 1
    finally:
        if acquisition_started:
            acquisition.stop()
        for artifact in acquisition.artifact_names:
            if (writer.path / artifact).is_file():
                writer.register_artifact(artifact)
        status = {
            RunState.COMPLETED: "complete",
            RunState.ABORTED: "aborted",
            RunState.FAILED: "failed",
        }.get(engine.state, "incomplete")
        writer.finalize(status)

    print(f"Session package: {writer.path}")
    if exit_code == 0:
        report = validate_session(writer.path)
        print(
            f"Session status: {report.status}; {report.event_count} events; "
            f"{report.trial_count} completed trials; {report.sample_count} EEG samples"
        )
    return exit_code


def _start_backend() -> tuple[subprocess.Popen[str], int]:
    process = subprocess.Popen(
        [sys.executable, "-m", "imagined_speech.ipc.server", "--host", "127.0.0.1", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
        env=_child_environment(),
    )
    assert process.stdout is not None
    line = process.stdout.readline().strip()
    if not line.startswith("PORT="):
        process.terminate()
        raise RuntimeError(f"backend did not publish its port: {line or 'no output'}")
    return process, int(line.removeprefix("PORT="))


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1])
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root + os.pathsep + existing if existing else source_root
    )
    return environment


def _stop_children(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


def _run_supervisor(args: argparse.Namespace) -> int:
    backend, port = _start_backend()
    operator_args = [
        sys.executable,
        "-m",
        "imagined_speech.cli",
        "_operator-client",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--config",
        str(args.config),
        "--participant",
        args.participant,
    ]
    if args.session_label:
        operator_args.extend(["--session-label", args.session_label])
    if args.output:
        operator_args.extend(["--output", str(args.output)])
    operator = subprocess.Popen(operator_args, env=_child_environment())
    try:
        while True:
            operator_code = operator.poll()
            if operator_code is not None:
                return operator_code
            backend_code = backend.poll()
            if backend_code is not None:
                return backend_code or 1
            time.sleep(0.05)
    finally:
        _stop_children([operator, backend])


def _run_subject_only(args: argparse.Namespace) -> int:
    from imagined_speech import __version__
    from imagined_speech.ipc.clients import JsonlClient
    from imagined_speech.ipc.messages import (
        ClientRole,
        CreateSessionPayload,
        HelloPayload,
        MessageType,
        message,
    )

    backend, port = _start_backend()
    subject = subprocess.Popen([
        sys.executable,
        "-m",
        "imagined_speech.cli",
        "_subject-client",
        "--port",
        str(port),
    ], env=_child_environment())
    client = JsonlClient("127.0.0.1", port)
    client.connect()
    client.send(message(
        MessageType.HELLO,
        HelloPayload(
            role=ClientRole.OPERATOR,
            software_version=__version__,
            process_id=os.getpid(),
        ),
    ))
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            incoming = client.receive_nowait()
            if incoming is not None and incoming.type == MessageType.HELLO_ACCEPTED:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("backend operator handshake timed out")
        create = message(
            MessageType.CREATE_SESSION,
            CreateSessionPayload(
                config_path=str(args.config),
                participant_id=args.participant,
                session_label=args.session_label,
                output_root=str(args.output) if args.output else None,
                auto_start=True,
                screen_index=args.screen,
                window_mode="PREVIOUS_POSITION" if args.windowed else None,
            ),
        )
        client.send(create)
        create_deadline = time.monotonic() + 5
        while True:
            incoming = client.receive_nowait()
            if incoming is None:
                if client.error is not None:
                    raise client.error
                time.sleep(0.02)
                continue
            if incoming.type == MessageType.SESSION_FINALIZED:
                print(f"Session package: {incoming.payload.get('session_path')}")
                return 0 if incoming.payload.get("state") == "finalized" else 1
            if incoming.type == MessageType.ERROR:
                error = str(incoming.payload.get("error"))
                if (
                    "subject process is not connected" in error
                    and time.monotonic() < create_deadline
                ):
                    time.sleep(0.1)
                    client.send(create)
                    continue
                raise RuntimeError(error)
    finally:
        try:
            client.send(message(MessageType.SHUTDOWN))
        except Exception:
            pass
        client.close()
        _stop_children([subject, backend])


def _recording_delay(
    seconds: float, clock: RealClock | VirtualClock, acquisition: AcquisitionRecorder
) -> None:
    if seconds <= 0:
        return
    if isinstance(clock, VirtualClock):
        clock.advance(seconds)
        acquisition.capture_available()
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.05, deadline - time.monotonic()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        parser.print_help()
        return 0

    args = parser.parse_args(arguments)
    if args.command == "_backend":
        from imagined_speech.ipc.server import main as backend_main

        return backend_main(["--host", args.host, "--port", str(args.port)])
    if args.command == "_subject-client":
        try:
            from imagined_speech.presentation.application import run_subject_process

            return run_subject_process(args.host, args.port)
        except ImportError as exc:
            print(
                "PsychoPy subject support is unavailable; install imagined-speech[subject] "
                "under Python 3.11",
                file=sys.stderr,
            )
            return 2
    if args.command == "_operator-client":
        try:
            from imagined_speech.ui.application import run_operator_application

            return run_operator_application(
                args.host,
                args.port,
                args.config,
                args.participant,
                args.session_label,
                args.output,
            )
        except ImportError:
            print(
                "Qt operator support is unavailable; install imagined-speech[ui]",
                file=sys.stderr,
            )
            return 2
    if args.command == "publish-lsl-synthetic":
        try:
            if args.duration is not None and args.duration <= 0:
                raise ValueError("publisher duration must be positive")
            profile = load_device_profile(args.device_profile)
            print(
                "Publishing synthetic LSL EEG; use Ctrl+C to stop...",
                flush=True,
            )
            samples = publish_synthetic_lsl(
                profile,
                duration_seconds=args.duration,
                stream_name=args.name,
            )
            print(f"Published {samples} samples")
            return 0
        except (ConfigurationError, ImportError, ValueError, RuntimeError) as exc:
            print(f"LSL publisher error: {exc}", file=sys.stderr)
            return 2
    if args.command == "validate-session":
        try:
            report = validate_session(args.session)
        except SessionValidationError as exc:
            print(f"Session validation error: {exc}", file=sys.stderr)
            return 2
        print(f"Valid session package: {report.session_path}")
        print(
            f"Status: {report.status}; events: {report.event_count}; "
            f"completed trials: {report.trial_count}; completed phases: {report.phase_count}; "
            f"EEG samples: {report.sample_count}"
        )
        for warning in report.warnings:
            print(f"Warning: {warning}")
        return 0
    if args.command == "run":
        try:
            load_experiment(args.config)
            return _run_supervisor(args)
        except (ConfigurationError, ValueError, RuntimeError) as exc:
            print(f"Experimenter UI error: {exc}", file=sys.stderr)
            return 2

    try:
        if args.command == "simulate":
            return _execute_session(args)
        if args.command == "run-subject":
            if args.clock != "real":
                raise ValueError(
                    "run-subject requires the real clock because PsychoPy flip timing "
                    "cannot run on virtual protocol time; use simulate for virtual time"
                )
            load_experiment(args.config)
            return _run_subject_only(args)
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


if __name__ == "__main__":
    raise SystemExit(main())
