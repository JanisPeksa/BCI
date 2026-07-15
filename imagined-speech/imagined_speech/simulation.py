"""Headless real-time and virtual-time protocol runners."""

from __future__ import annotations

import time

from imagined_speech.engine import ProtocolEngine, RunState, VirtualClock


def run_virtual(engine: ProtocolEngine) -> None:
    if not isinstance(engine.clock, VirtualClock):
        raise TypeError("virtual runner requires a VirtualClock")
    engine.start()
    while engine.state == RunState.RUNNING:
        engine.clock.advance(engine.remaining_seconds)
        engine.tick()


def run_real(engine: ProtocolEngine, poll_interval_seconds: float = 0.02) -> None:
    if poll_interval_seconds <= 0:
        raise ValueError("poll interval must be positive")
    engine.start()
    while engine.state == RunState.RUNNING:
        engine.tick()
        if engine.state == RunState.RUNNING:
            time.sleep(min(poll_interval_seconds, max(engine.remaining_seconds, 0.001)))
