"""Headless real-time and virtual-time protocol runners."""

from __future__ import annotations

import time
from collections.abc import Callable

from imagined_speech.runtime.clock import VirtualClock
from imagined_speech.runtime.protocol import (
    FrameLockedProtocolEngine,
    ProtocolEngine,
    RunState,
    TERMINAL_STATES,
)


def run_virtual(engine: ProtocolEngine) -> None:
    if not isinstance(engine.clock, VirtualClock):
        raise TypeError("virtual runner requires a VirtualClock")
    engine.start()
    while engine.state == RunState.RUNNING:
        engine.clock.advance(engine.remaining_seconds)
        engine.tick()


def run_virtual_presentation(
    engine: FrameLockedProtocolEngine,
    record_timing: Callable[[dict[str, object]], None],
) -> None:
    """Drive the production presentation handshake without a real window."""
    if not isinstance(engine.clock, VirtualClock):
        raise TypeError("virtual presentation requires a VirtualClock")
    engine.start()
    frame_index = 0
    while engine.state not in TERMINAL_STATES:
        if engine.state == RunState.AWAITING_PRESENTATION:
            previous_id = None
            presentation_id = engine.presentation_id
            neutral = False
        elif engine.state == RunState.RUNNING:
            previous_id = engine.presentation_id
            successor = engine.successor_view_state()
            presentation_id = successor.presentation_id if successor else None
            neutral = successor is None
            engine.clock.advance(engine.remaining_seconds)
        else:
            raise RuntimeError(
                f"virtual presentation cannot drive {engine.state.value} without a command"
            )
        frame_index += 1
        occurrence = engine.clock.monotonic()
        revision = engine.presentation_revision
        record_timing({
            "kind": "presentation_request",
            "driver": "virtual",
            "revision": revision,
            "current_presentation_id": previous_id,
            "successor_presentation_id": presentation_id,
        })
        engine.acknowledge_frame(
            previous_presentation_id=previous_id,
            presentation_id=presentation_id,
            revision=revision,
            monotonic_seconds=occurrence,
            wall_time_utc=engine.clock.wall_time_utc(),
            neutral=neutral,
        )
        record_timing({
            "kind": "frame_acknowledgement",
            "driver": "virtual",
            "accepted": True,
            "revision": revision,
            "previous_presentation_id": previous_id,
            "presentation_id": presentation_id,
            "neutral": neutral,
            "frame_index": frame_index,
            "occurrence_monotonic_seconds": occurrence,
            "wall_time_utc": engine.clock.wall_time_utc().isoformat(),
        })


def run_real(engine: ProtocolEngine, poll_interval_seconds: float = 0.02) -> None:
    if poll_interval_seconds <= 0:
        raise ValueError("poll interval must be positive")
    engine.start()
    while engine.state == RunState.RUNNING:
        engine.tick()
        if engine.state == RunState.RUNNING:
            time.sleep(min(poll_interval_seconds, max(engine.remaining_seconds, 0.001)))
