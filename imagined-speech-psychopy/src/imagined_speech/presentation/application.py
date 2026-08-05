"""Long-lived PsychoPy subject process controlled exclusively over JSONL IPC."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from imagined_speech import __version__
from imagined_speech.ipc.clients import JsonlClient
from imagined_speech.ipc.messages import (
    ClientRole,
    ClockPingPayload,
    ClockPongPayload,
    FrameAcknowledgementPayload,
    FrameTimingPayload,
    HelloPayload,
    MessageType,
    SubjectAbortPayload,
    TimingPreflightPayload,
    message,
)
from imagined_speech.presentation.audio import AudioScheduler
from imagined_speech.presentation.renderer import PsychopyRenderer, create_window
from imagined_speech.presentation.timing import run_preflight


def _ack(
    client: JsonlClient,
    *,
    session_id: str,
    revision: int,
    previous_id: str | None,
    current_id: str | None,
    neutral: bool,
    flip_time: float | None,
    frame_index: int,
    dropped_frames: int,
    frame_interval_seconds: float | None = None,
    frame_intervals_seconds: tuple[float, ...] = (),
    audio_time: float | None = None,
    audio_started: bool = False,
) -> None:
    client.send(message(
        MessageType.FRAME_ACK,
        FrameAcknowledgementPayload(
            previous_presentation_id=previous_id,
            presentation_id=current_id,
            neutral=neutral,
            flip_time=flip_time,
            subject_monotonic_ns=time.perf_counter_ns(),
            wall_time_utc=datetime.now(UTC),
            frame_index=frame_index,
            dropped_frames=dropped_frames,
            frame_interval_seconds=frame_interval_seconds,
            frame_intervals_seconds=frame_intervals_seconds,
            audio_scheduled_time=audio_time,
            audio_started=audio_started,
        ),
        session_id=session_id,
        revision=revision,
    ))


def _intervals_since(window, cursor: int) -> tuple[tuple[float, ...], int]:
    intervals = tuple(float(value) for value in window.frameIntervals[cursor:])
    return intervals, len(window.frameIntervals)


def _dropped_batch_since(window, cursor: int) -> tuple[tuple[float, ...], int]:
    if len(window.frameIntervals) <= cursor:
        return (), cursor
    if float(window.frameIntervals[-1]) <= float(window.refreshThreshold):
        return (), cursor
    return _intervals_since(window, cursor)


def _send_frame_timing(
    client: JsonlClient,
    *,
    session_id: str,
    revision: int,
    presentation_id: str,
    frame_index: int,
    dropped_frames: int,
    intervals: tuple[float, ...],
) -> None:
    client.send(message(
        MessageType.FRAME_TIMING,
        FrameTimingPayload(
            presentation_id=presentation_id,
            subject_monotonic_ns=time.perf_counter_ns(),
            wall_time_utc=datetime.now(UTC),
            frame_index=frame_index,
            dropped_frames=dropped_frames,
            frame_intervals_seconds=intervals,
        ),
        session_id=session_id,
        revision=revision,
    ))


def run_subject_process(host: str, port: int) -> int:
    import platform

    import psychopy
    from psychopy import core, event

    client = JsonlClient(host, port)
    client.connect()
    client.send(message(
        MessageType.HELLO,
        HelloPayload(
            role=ClientRole.SUBJECT,
            software_version=__version__,
            process_id=os.getpid(),
        ),
    ))
    renderer = None
    audio = None
    active: dict | None = None
    successor: dict | None = None
    session_id: str | None = None
    revision = 0
    onset_monotonic = 0.0
    frame_index = 0
    frame_interval_cursor = 0
    try:
        while True:
            incoming = client.receive_nowait()
            while incoming is not None:
                if incoming.type == MessageType.CLOCK_PING:
                    ping = ClockPingPayload.model_validate(incoming.payload)
                    received = time.perf_counter_ns()
                    client.send(message(
                        MessageType.CLOCK_PONG,
                        ClockPongPayload(
                            **ping.model_dump(),
                            subject_receive_monotonic_ns=received,
                            subject_send_monotonic_ns=time.perf_counter_ns(),
                        ),
                    ))
                elif incoming.type == MessageType.SUBJECT_INIT:
                    if renderer is not None:
                        renderer.window.close()
                    session_id = incoming.session_id
                    presentation = incoming.payload["presentation"]
                    assets = incoming.payload.get("assets", {})
                    presentation_assets = incoming.payload.get(
                        "presentation_assets", {}
                    )
                    error = None
                    window = None
                    try:
                        window = create_window(
                            presentation["psychopy"],
                            incoming.payload.get("display_target"),
                        )
                        renderer = PsychopyRenderer(
                            window,
                            presentation,
                            assets,
                            presentation_assets,
                        )
                        audio = AudioScheduler(
                            presentation["audio"]["enabled"],
                            presentation["audio"]["volume"],
                            presentation["psychopy"]["audio_latency_mode"],
                        )
                        audio.preload(assets)
                        result = run_preflight(window, presentation["psychopy"])
                        result["metadata"].update({
                            "psychopy_version": psychopy.__version__,
                            "python_version": platform.python_version(),
                            "window_size_px": [int(value) for value in window.size],
                            "screen_index": presentation["psychopy"]["screen_index"],
                            "audio_backend": "ptb",
                            **getattr(
                                window,
                                "_imagined_speech_monitor_metadata",
                                {},
                            ),
                        })
                        frame_index = 0
                        frame_interval_cursor = 0
                    except Exception as exc:
                        metadata = dict(getattr(exc, "metadata", {}))
                        if window is not None:
                            metadata.update(getattr(
                                window,
                                "_imagined_speech_monitor_metadata",
                                {},
                            ))
                            window.close()
                        renderer = None
                        audio = None
                        result = {
                            "passed": False,
                            "measured_refresh_rate_hz": 0,
                            "dropped_frame_fraction": 1,
                            "frame_interval_count": 0,
                            "frame_intervals_seconds": (),
                            "metadata": metadata,
                        }
                        error = str(exc)
                    client.send(message(
                        MessageType.TIMING_PREFLIGHT,
                        TimingPreflightPayload(**result, error=error),
                        session_id=session_id,
                    ))
                elif incoming.type == MessageType.PRESENTATION_REQUEST:
                    if renderer is None or incoming.session_id != session_id:
                        raise RuntimeError("presentation request arrived before subject initialization")
                    requested = incoming.payload.get("current")
                    requested_successor = incoming.payload.get("successor")
                    if active is not None and requested and requested.get("presentation_id") == active.get("presentation_id"):
                        successor = requested_successor
                        revision = incoming.revision
                    else:
                        previous = active.get("presentation_id") if active else None
                        active = requested
                        successor = requested_successor
                        revision = incoming.revision
                        audio_time, audio_started = audio.transition(
                            active,
                            renderer.window,
                        ) if audio else (None, False)
                        renderer.draw(active)
                        was_recording = bool(renderer.window.recordFrameIntervals)
                        if active is not None and not was_recording:
                            renderer.window.frameIntervals = []
                            renderer.window.recordFrameIntervals = True
                            frame_interval_cursor = 0
                        flip_time = renderer.flip()
                        frame_intervals, frame_interval_cursor = _intervals_since(
                            renderer.window, frame_interval_cursor
                        )
                        frame_interval = frame_intervals[-1] if frame_intervals else None
                        frame_index += 1
                        onset_monotonic = time.perf_counter()
                        _ack(
                            client,
                            session_id=session_id or "",
                            revision=revision,
                            previous_id=previous,
                            current_id=active.get("presentation_id") if active else None,
                            neutral=active is None,
                            flip_time=flip_time,
                            frame_index=frame_index,
                            dropped_frames=int(renderer.window.nDroppedFrames),
                            frame_interval_seconds=frame_interval,
                            frame_intervals_seconds=frame_intervals,
                            audio_time=audio_time,
                            audio_started=audio_started,
                        )
                elif incoming.type == MessageType.PRESENTATION_INTERRUPT:
                    if renderer is not None and session_id is not None:
                        previous = active.get("presentation_id") if active else None
                        if audio:
                            audio.stop_all()
                        renderer.draw(None)
                        flip_time = renderer.neutral()
                        frame_intervals, frame_interval_cursor = _intervals_since(
                            renderer.window, frame_interval_cursor
                        )
                        frame_interval = frame_intervals[-1] if frame_intervals else None
                        renderer.window.recordFrameIntervals = False
                        frame_index += 1
                        active = None
                        successor = None
                        revision = incoming.revision
                        _ack(
                            client,
                            session_id=session_id,
                            revision=revision,
                            previous_id=previous,
                            current_id=None,
                            neutral=True,
                            flip_time=flip_time,
                            frame_index=frame_index,
                            dropped_frames=int(renderer.window.nDroppedFrames),
                            frame_interval_seconds=frame_interval,
                            frame_intervals_seconds=frame_intervals,
                        )
                elif incoming.type == MessageType.SESSION_FINALIZED:
                    if incoming.session_id == session_id:
                        if audio:
                            audio.stop_all()
                        if renderer is not None:
                            renderer.window.recordFrameIntervals = False
                            renderer.window.close()
                        return 0
                elif incoming.type == MessageType.SHUTDOWN:
                    return 0
                incoming = client.receive_nowait()

            if renderer is not None and active is not None:
                duration = float(active.get("duration_seconds", 0))
                elapsed = time.perf_counter() - onset_monotonic
                if duration > 0 and elapsed >= duration:
                    previous = active.get("presentation_id")
                    active = successor
                    successor = None
                    audio_time, audio_started = audio.transition(
                        active,
                        renderer.window,
                    ) if audio else (None, False)
                    renderer.draw(active)
                    flip_time = renderer.flip()
                    frame_intervals, frame_interval_cursor = _intervals_since(
                        renderer.window, frame_interval_cursor
                    )
                    frame_interval = frame_intervals[-1] if frame_intervals else None
                    if active is None:
                        renderer.window.recordFrameIntervals = False
                    frame_index += 1
                    onset_monotonic = time.perf_counter()
                    _ack(
                        client,
                        session_id=session_id or "",
                        revision=revision,
                        previous_id=previous,
                        current_id=active.get("presentation_id") if active else None,
                        neutral=active is None,
                        flip_time=flip_time,
                        frame_index=frame_index,
                        dropped_frames=int(renderer.window.nDroppedFrames),
                        frame_interval_seconds=frame_interval,
                        frame_intervals_seconds=frame_intervals,
                        audio_time=audio_time,
                        audio_started=audio_started,
                    )
                else:
                    renderer.draw(active, max(0.0, duration - elapsed))
                    renderer.flip()
                    frame_index += 1
                    dropped_batch, next_cursor = _dropped_batch_since(
                        renderer.window, frame_interval_cursor
                    )
                    if dropped_batch:
                        frame_interval_cursor = next_cursor
                        _send_frame_timing(
                            client,
                            session_id=session_id or "",
                            revision=revision,
                            presentation_id=active.get("presentation_id") or "",
                            frame_index=frame_index,
                            dropped_frames=int(renderer.window.nDroppedFrames),
                            intervals=dropped_batch,
                        )
            if event.getKeys(["escape"]):
                if renderer is not None and session_id is not None:
                    previous = active.get("presentation_id") if active else None
                    flip_time = renderer.neutral()
                    frame_intervals, frame_interval_cursor = _intervals_since(
                        renderer.window, frame_interval_cursor
                    )
                    frame_interval = frame_intervals[-1] if frame_intervals else None
                    renderer.window.recordFrameIntervals = False
                    frame_index += 1
                    acknowledgement = FrameAcknowledgementPayload(
                        previous_presentation_id=previous,
                        presentation_id=None,
                        neutral=True,
                        flip_time=flip_time,
                        subject_monotonic_ns=time.perf_counter_ns(),
                        wall_time_utc=datetime.now(UTC),
                        frame_index=frame_index,
                        dropped_frames=int(renderer.window.nDroppedFrames),
                        frame_interval_seconds=frame_interval,
                        frame_intervals_seconds=frame_intervals,
                    )
                    client.send(message(
                        MessageType.SUBJECT_ABORT,
                        SubjectAbortPayload(
                            reason="Escape pressed",
                            acknowledgement=acknowledgement,
                        ),
                        session_id=session_id,
                        revision=revision,
                    ))
                    active = None
                    successor = None
            if renderer is None or active is None:
                core.wait(0.002)
            if client.error is not None:
                raise client.error
    finally:
        if audio:
            audio.stop_all()
        if renderer is not None:
            renderer.window.close()
        client.close()
