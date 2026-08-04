"""Async loopback backend owning zero or one active session runtime."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path

from imagined_speech import __version__
from imagined_speech.config import load_experiment
from imagined_speech.events import EventSource
from imagined_speech.ipc.framing import MAX_LINE_BYTES, FramingError, decode_envelope, encode_envelope
from imagined_speech.ipc.messages import (
    ClientRole,
    ClockPingPayload,
    ClockPongPayload,
    CreateSessionPayload,
    Envelope,
    FrameAcknowledgementPayload,
    HelloPayload,
    MessageType,
    OperatorCommandPayload,
    SubjectAbortPayload,
    TimingPreflightPayload,
    message,
)
from imagined_speech.presentation.timing import (
    ClockCalibration,
    best_calibration,
    calculate_calibration,
)
from imagined_speech.runtime.commands import OperatorCommand
from imagined_speech.runtime.coordinator import SessionRuntime, SessionRuntimeState
from imagined_speech.runtime.protocol import TERMINAL_STATES


async def _close_stream_writer(writer: asyncio.StreamWriter) -> None:
    """Close a stream without surfacing normal peer-reset errors on Windows."""
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        # Proactor transports commonly report WinError 64/10054 here when the
        # GUI or PsychoPy process has already closed its side of the socket.
        pass


class _Connection:
    def __init__(self, role: ClientRole, writer: asyncio.StreamWriter) -> None:
        self.role = role
        self.writer = writer
        self.queue: asyncio.Queue[Envelope | None] = asyncio.Queue(maxsize=256)
        self.task = asyncio.create_task(self._write_loop())
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def send(self, value: Envelope, *, critical: bool = True) -> None:
        if self._closed:
            raise ConnectionError(f"{self.role.value} connection is closed")
        if critical:
            await self.queue.put(value)
            return
        # Operator snapshots are replaceable state. Keep at most one queued
        # snapshot so a slow console catches up to the newest revision without
        # displacing presentation commands or final notifications.
        queued = self.queue._queue  # type: ignore[attr-defined]
        for index in range(len(queued) - 1, -1, -1):
            candidate = queued[index]
            if isinstance(candidate, Envelope) and candidate.type == value.type:
                queued[index] = value
                return
        try:
            self.queue.put_nowait(value)
        except asyncio.QueueFull:
            return

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.queue.put_nowait(None)
            except asyncio.QueueFull:
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            await _close_stream_writer(self.writer)

    async def _write_loop(self) -> None:
        while True:
            value = await self.queue.get()
            if value is None:
                return
            self.writer.write(encode_envelope(value))
            await self.writer.drain()


class BackendService:
    def __init__(self) -> None:
        self.connections: dict[ClientRole, _Connection] = {}
        self.runtime: SessionRuntime | None = None
        self._last_snapshot = 0.0
        self._final_notified = False
        self._auto_start = False
        self._last_presentation_signature: tuple[object, ...] | None = None
        self._shutdown = asyncio.Event()
        self._shutdown_requested = False
        self._clock_phase: str | None = None
        self._clock_sequence = 0
        self._clock_samples: list[ClockCalibration] = []
        self._calibrations: dict[str, ClockCalibration] = {}
        self._calibration_samples: dict[str, list[ClockCalibration]] = {}
        self._subject_init: Envelope | None = None

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> int:
        server = await asyncio.start_server(
            self._handle_connection,
            host,
            port,
            limit=MAX_LINE_BYTES + 1,
        )
        selected = int(server.sockets[0].getsockname()[1])
        print(f"PORT={selected}", flush=True)
        ticker = asyncio.create_task(self._tick_loop())
        await self._shutdown.wait()
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        server.close()
        await self._broadcast(message(MessageType.SHUTDOWN))
        if self.runtime is not None:
            self.runtime.close()
        for connection in tuple(self.connections.values()):
            await connection.close()
        await server.wait_closed()
        return selected

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection: _Connection | None = None
        try:
            line = await asyncio.wait_for(
                reader.readline(), timeout=5
            )
            if not line:
                return
            first = decode_envelope(line)
            if first.type != MessageType.HELLO:
                raise FramingError("first message must be hello")
            hello = HelloPayload.model_validate(first.payload)
            if hello.role in self.connections:
                raise FramingError(f"a {hello.role.value} client is already connected")
            connection = _Connection(hello.role, writer)
            self.connections[hello.role] = connection
            await connection.send(message(
                MessageType.HELLO_ACCEPTED,
                {
                    "role": hello.role.value,
                    "software_version": __version__,
                    "backend_process_id": os.getpid(),
                },
            ))
            if hello.role == ClientRole.SUBJECT:
                await self._begin_clock_calibration("initial")
            while True:
                line = await reader.readline()
                if not line:
                    break
                incoming = decode_envelope(line)
                try:
                    await self._dispatch(connection, incoming)
                except (ValueError, RuntimeError) as exc:
                    await connection.send(message(
                        MessageType.ERROR,
                        {"error": str(exc), "in_reply_to": incoming.message_id},
                    ))
        except (ConnectionError, OSError):
            # A process exiting or closing its GUI can reset a Windows socket
            # before the TCP close handshake completes. The finally block still
            # applies the configured disconnect policy and releases resources.
            pass
        except (FramingError, ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            value = message(MessageType.ERROR, {"error": str(exc)})
            try:
                if connection is not None:
                    await connection.send(value)
                else:
                    writer.write(encode_envelope(value))
                    await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            if connection is not None:
                self.connections.pop(connection.role, None)
                await self._on_disconnect(connection.role)
                await connection.close()
            elif not writer.is_closing():
                await _close_stream_writer(writer)

    async def _dispatch(self, connection: _Connection, value: Envelope) -> None:
        if value.type == MessageType.CREATE_SESSION:
            self._require_role(connection, ClientRole.OPERATOR)
            await self._create_session(CreateSessionPayload.model_validate(value.payload))
        elif value.type == MessageType.TIMING_PREFLIGHT:
            self._require_role(connection, ClientRole.SUBJECT)
            await self._timing_preflight(value)
        elif value.type == MessageType.OPERATOR_COMMAND:
            self._require_role(connection, ClientRole.OPERATOR)
            await self._operator_command(value)
        elif value.type == MessageType.FRAME_ACK:
            self._require_role(connection, ClientRole.SUBJECT)
            await self._frame_ack(value)
        elif value.type == MessageType.SUBJECT_ABORT:
            self._require_role(connection, ClientRole.SUBJECT)
            await self._subject_abort(value)
        elif value.type == MessageType.CLOCK_PONG:
            self._require_role(connection, ClientRole.SUBJECT)
            await self._clock_pong(value)
        elif value.type == MessageType.SHUTDOWN:
            self._require_role(connection, ClientRole.OPERATOR)
            await self._request_shutdown()
        elif value.type == MessageType.HELLO:
            raise FramingError("hello may only be sent once")
        else:
            raise FramingError(f"message type {value.type.value} is not valid from {connection.role.value}")

    async def _create_session(self, payload: CreateSessionPayload) -> None:
        if self.runtime is not None and self.runtime.state not in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            raise RuntimeError("a session is already active")
        subject = self.connections.get(ClientRole.SUBJECT)
        if subject is None:
            raise RuntimeError("the PsychoPy subject process is not connected")
        resolved = load_experiment(payload.config_path)
        psychopy_updates: dict[str, object] = {}
        if payload.screen_index is not None:
            psychopy_updates["screen_index"] = payload.screen_index
        if payload.window_mode is not None:
            psychopy_updates["window_mode"] = payload.window_mode
        if psychopy_updates:
            psychopy = resolved.config.presentation.psychopy.model_copy(
                update=psychopy_updates
            )
            presentation = resolved.config.presentation.model_copy(
                update={"psychopy": psychopy}
            )
            resolved = replace(
                resolved,
                config=resolved.config.model_copy(update={"presentation": presentation}),
            )
        self.runtime = SessionRuntime(
            resolved,
            payload.participant_id,
            output_root=Path(payload.output_root) if payload.output_root else None,
            session_label=payload.session_label,
            frame_locked=True,
        )
        self.runtime.writer.record_presentation_timing({
            "kind": "subject_initialization_requested",
            "backend_monotonic_ns": time.perf_counter_ns(),
        })
        self._final_notified = False
        self._auto_start = payload.auto_start
        self._last_presentation_signature = None
        self._subject_init = message(
            MessageType.SUBJECT_INIT,
            {
                "presentation": resolved.config.presentation.model_dump(mode="json"),
                "stimuli": [value.model_dump(mode="json") for value in resolved.config.stimuli],
                "assets": {
                    stimulus_id: {name: str(path) for name, path in values.items()}
                    for stimulus_id, values in resolved.assets.items()
                },
            },
            session_id=self.runtime.writer.session_id,
        )
        if self._clock_phase != "initial":
            self._calibrations.pop("initial", None)
            await self._begin_clock_calibration("initial")
        if payload.auto_start:
            self.runtime.writer.record_presentation_timing({"kind": "auto_start_requested"})

    async def _timing_preflight(self, value: Envelope) -> None:
        runtime = self._require_runtime(value.session_id)
        result = TimingPreflightPayload.model_validate(value.payload)
        calibration = self._calibrations.get("initial")
        maximum_rtt_ns = int(
            runtime.resolved.config.presentation.psychopy.max_ipc_rtt_ms * 1_000_000
        )
        if calibration is None or calibration.round_trip_ns > maximum_rtt_ns:
            reason = (
                "clock calibration did not complete"
                if calibration is None
                else (
                    f"IPC round trip {calibration.round_trip_ns / 1_000_000:.3f} ms "
                    f"exceeds {maximum_rtt_ns / 1_000_000:.3f} ms"
                )
            )
            result = result.model_copy(update={
                "passed": False,
                "error": result.error or reason,
                "metadata": {**result.metadata, "ipc_timing_ok": False},
            })
        runtime.record_timing_preflight(result)
        if runtime.state != SessionRuntimeState.FAILED:
            if runtime.state == SessionRuntimeState.CREATED:
                runtime.start()
            elif runtime.state != SessionRuntimeState.READY:
                raise RuntimeError(
                    f"timing preflight is not valid from {runtime.state.value}"
                )
            await self._broadcast(message(
                MessageType.SESSION_READY,
                {
                    "session_path": str(runtime.session_path),
                    "participant_id": runtime.writer.participant_id,
                },
                session_id=runtime.writer.session_id,
            ))
            if self._auto_start:
                runtime.execute(OperatorCommand.START_PROTOCOL, source="system")

    async def _operator_command(self, value: Envelope) -> None:
        runtime = self._require_runtime(value.session_id)
        payload = OperatorCommandPayload.model_validate(value.payload)
        record = runtime.execute(OperatorCommand(payload.command), note=payload.note)
        await self._send_role(ClientRole.OPERATOR, message(
            MessageType.OPERATOR_COMMAND_RESULT,
            record.model_dump(mode="json"),
            session_id=runtime.writer.session_id,
        ))
        await self._send_presentation_state()

    async def _frame_ack(self, value: Envelope) -> None:
        runtime = self._require_runtime(value.session_id)
        ack = FrameAcknowledgementPayload.model_validate(value.payload)
        try:
            runtime.acknowledge_frame(ack, revision=value.revision)
        except (ValueError, RuntimeError) as exc:
            diagnostic = {
                "kind": "frame_acknowledgement_rejected",
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "received_revision": value.revision,
                "received_previous_presentation_id": ack.previous_presentation_id,
                "received_presentation_id": ack.presentation_id,
                "received_neutral": ack.neutral,
                "engine": runtime.engine.diagnostic_state(),
            }
            await self._send_role(ClientRole.SUBJECT, message(
                MessageType.ERROR,
                {**diagnostic, "in_reply_to": value.message_id},
                session_id=runtime.writer.session_id,
            ))
            await self._send_role(ClientRole.OPERATOR, message(
                MessageType.ERROR,
                diagnostic,
                session_id=runtime.writer.session_id,
            ))
            return
        await self._send_presentation_state()

    async def _subject_abort(self, value: Envelope) -> None:
        runtime = self._require_runtime(value.session_id)
        payload = SubjectAbortPayload.model_validate(value.payload)
        try:
            runtime.confirm_subject_abort(
                payload.acknowledgement,
                revision=value.revision,
                reason=payload.reason,
            )
        except (ValueError, RuntimeError) as exc:
            runtime.engine.fail(f"invalid subject abort acknowledgement: {exc}")
        await self._send_presentation_state()

    async def _send_presentation_state(self) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        state = runtime.presentation_state()
        if state is None:
            return
        signature = (
            state["revision"],
            state["interrupt"],
            runtime.engine.state.value,
        )
        if signature == self._last_presentation_signature:
            return
        self._last_presentation_signature = signature
        current = state.get("current") or {}
        successor = state.get("successor") or {}
        runtime.writer.record_presentation_timing({
            "kind": "presentation_request",
            "revision": state["revision"],
            "interrupt": state["interrupt"],
            "current_presentation_id": current.get("presentation_id"),
            "successor_presentation_id": successor.get("presentation_id"),
            "engine": runtime.engine.diagnostic_state(),
            "backend_monotonic_ns": time.perf_counter_ns(),
        })
        if state["interrupt"]:
            await self._send_role(ClientRole.SUBJECT, message(
                MessageType.PRESENTATION_INTERRUPT,
                {},
                session_id=runtime.writer.session_id,
                revision=int(state["revision"]),
            ))
        else:
            await self._send_role(ClientRole.SUBJECT, message(
                MessageType.PRESENTATION_REQUEST,
                {"current": state["current"], "successor": state["successor"]},
                session_id=runtime.writer.session_id,
                revision=int(state["revision"]),
            ))

    async def _tick_loop(self) -> None:
        while not self._shutdown.is_set():
            runtime = self.runtime
            if runtime is not None:
                try:
                    runtime.tick()
                except Exception as exc:
                    runtime.error = str(exc)
                    runtime.writer.record_presentation_timing({
                        "kind": "backend_tick_exception",
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                        "engine": runtime.engine.diagnostic_state(),
                        "backend_monotonic_ns": time.perf_counter_ns(),
                    })
                    runtime.engine.fail(str(exc))
                if runtime.needs_final_clock_calibration and self._clock_phase is None:
                    if ClientRole.SUBJECT in self.connections:
                        await self._begin_clock_calibration("final")
                    else:
                        runtime.engine.fail("subject unavailable for final clock calibration")
                        runtime.final_clock_calibrated = True
                        runtime.tick()
                state = runtime.presentation_state()
                if state is not None:
                    await self._send_presentation_state()
                if time.monotonic() - self._last_snapshot >= 0.1:
                    self._last_snapshot = time.monotonic()
                    await self._send_operator_snapshot(runtime)
                if runtime.state in {
                    SessionRuntimeState.FINALIZED,
                    SessionRuntimeState.FAILED,
                } and not self._final_notified:
                    self._final_notified = True
                    report = runtime.validation_report
                    await self._broadcast(message(
                        MessageType.SESSION_FINALIZED,
                        {
                            "state": runtime.state.value,
                            "session_path": str(runtime.session_path),
                            "error": runtime.error or runtime.engine.failure_reason,
                            "engine": runtime.engine.diagnostic_state(),
                            "validation": asdict(report) if report else None,
                        },
                        session_id=runtime.writer.session_id,
                    ))
                    if self._shutdown_requested:
                        await self._send_role(
                            ClientRole.SUBJECT,
                            message(MessageType.SHUTDOWN),
                        )
                        self._shutdown.set()
            await asyncio.sleep(0.02)

    async def _request_shutdown(self) -> None:
        self._shutdown_requested = True
        runtime = self.runtime
        if runtime is None or runtime.state in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            await self._send_role(ClientRole.SUBJECT, message(MessageType.SHUTDOWN))
            self._shutdown.set()
            return
        if runtime.engine.state not in TERMINAL_STATES:
            try:
                runtime.engine.abort(EventSource.EXPERIMENTER_UI)
            except RuntimeError:
                runtime.engine.fail("shutdown could not request a controlled abort")
        await self._send_presentation_state()

    async def _send_operator_snapshot(self, runtime: SessionRuntime) -> None:
        engine_view = asdict(runtime.engine.view_state())
        engine_view["run_state"] = runtime.engine.view_state().run_state.value
        snapshot = asdict(runtime.acquisition_snapshot())
        await self._send_role(ClientRole.OPERATOR, message(
            MessageType.OPERATOR_STATE,
            {
                "runtime_state": runtime.state.value,
                "engine_state": runtime.engine.state.value,
                "protocol_started": runtime.protocol_started,
                "recording": runtime.recording,
                "session_path": str(runtime.session_path),
                "session_id": runtime.writer.session_id,
                "participant_id": runtime.writer.participant_id,
                "view_state": engine_view,
                "acquisition": snapshot,
                "events": [event.model_dump(mode="json") for event in runtime.event_memory.events[-100:]],
                "operator_records": [value.model_dump(mode="json") for value in runtime.operator_records[-100:]],
                "timing_warnings": list(runtime.timing_warnings),
                "error": runtime.error or runtime.engine.failure_reason,
                "engine_diagnostics": runtime.engine.diagnostic_state(),
            },
            session_id=runtime.writer.session_id,
        ), critical=False)

    async def _on_disconnect(self, role: ClientRole) -> None:
        runtime = self.runtime
        if runtime is None or runtime.state in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            return
        if role == ClientRole.SUBJECT and runtime.protocol_started:
            runtime.engine.fail("PsychoPy subject process disconnected")
        elif role == ClientRole.OPERATOR and runtime.recording:
            await asyncio.sleep(2)
            if (
                not self._shutdown_requested
                and ClientRole.OPERATOR not in self.connections
                and self.runtime is runtime
            ):
                try:
                    runtime.engine.abort(EventSource.SYSTEM)
                    await self._send_presentation_state()
                except RuntimeError:
                    runtime.engine.fail("operator process disconnected")

    async def _begin_clock_calibration(self, phase: str) -> None:
        self._clock_phase = phase
        self._clock_sequence = 0
        self._clock_samples = []
        await self._send_clock_ping()

    async def _send_clock_ping(self) -> None:
        if self._clock_phase is None:
            return
        payload = ClockPingPayload(
            sequence=self._clock_sequence,
            backend_send_monotonic_ns=time.perf_counter_ns(),
        )
        await self._send_role(ClientRole.SUBJECT, message(MessageType.CLOCK_PING, payload))

    async def _clock_pong(self, value: Envelope) -> None:
        if self._clock_phase is None:
            raise RuntimeError("no clock calibration is active")
        payload = ClockPongPayload.model_validate(value.payload)
        if payload.sequence != self._clock_sequence:
            raise RuntimeError("out-of-order clock calibration response")
        sample = calculate_calibration(
            sequence=payload.sequence,
            backend_send_ns=payload.backend_send_monotonic_ns,
            subject_receive_ns=payload.subject_receive_monotonic_ns,
            subject_send_ns=payload.subject_send_monotonic_ns,
            backend_receive_ns=time.perf_counter_ns(),
        )
        self._clock_samples.append(sample)
        self._clock_sequence += 1
        if self._clock_sequence < 5:
            await self._send_clock_ping()
            return
        phase = self._clock_phase
        best = best_calibration(self._clock_samples)
        assert best is not None
        self._calibrations[phase] = best
        self._calibration_samples[phase] = list(self._clock_samples)
        self._clock_phase = None
        self._record_calibration(phase, best)
        if (
            phase == "initial"
            and self._subject_init is not None
            and self.runtime is not None
            and not self.runtime.protocol_started
            and self.runtime.state in {
                SessionRuntimeState.CREATED,
                SessionRuntimeState.READY,
            }
        ):
            await self._send_role(ClientRole.SUBJECT, self._subject_init)
        if phase == "final" and self.runtime is not None:
            self.runtime.tick()

    def _record_calibration(self, phase: str, best: ClockCalibration) -> None:
        runtime = self.runtime
        if runtime is None or runtime.state in {
            SessionRuntimeState.FINALIZED,
            SessionRuntimeState.FAILED,
        }:
            return
        runtime.record_clock_calibration(
            phase=phase,
            offset_ns=best.offset_ns,
            round_trip_ns=best.round_trip_ns,
            samples=[asdict(value) for value in self._calibration_samples.get(phase, [])],
        )

    def _require_runtime(self, session_id: str | None) -> SessionRuntime:
        if self.runtime is None:
            raise RuntimeError("no session exists")
        if session_id != self.runtime.writer.session_id:
            raise RuntimeError("message references a stale session")
        return self.runtime

    @staticmethod
    def _require_role(connection: _Connection, role: ClientRole) -> None:
        if connection.role != role:
            raise RuntimeError(f"message requires the {role.value} role")

    async def _send_role(
        self, role: ClientRole, value: Envelope, *, critical: bool = True
    ) -> None:
        connection = self.connections.get(role)
        if connection is not None:
            try:
                await connection.send(value, critical=critical)
            except (ConnectionError, OSError):
                pass

    async def _broadcast(self, value: Envelope) -> None:
        for connection in tuple(self.connections.values()):
            try:
                await connection.send(value)
            except (ConnectionError, OSError):
                pass


async def run_backend(host: str = "127.0.0.1", port: int = 0) -> int:
    service = BackendService()
    await service.serve(host, port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imagined-speech-backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    return asyncio.run(run_backend(args.host, args.port))


if __name__ == "__main__":
    raise SystemExit(main())
