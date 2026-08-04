"""Interchangeable real and deterministic protocol clocks."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class ProtocolClock(Protocol):
    def monotonic(self) -> float: ...

    def wall_time_utc(self) -> datetime: ...


class RealClock:
    def monotonic(self) -> float:
        # IPC clock calibration and flip acknowledgements use perf_counter_ns.
        # Use the same clock domain for protocol deadlines; on Python 3.11 on
        # Windows, monotonic() and perf_counter() can have different origins.
        return time.perf_counter()

    def wall_time_utc(self) -> datetime:
        return datetime.now(UTC)


class VirtualClock:
    def __init__(self, wall_origin: datetime | None = None) -> None:
        self._monotonic = 0.0
        self._wall_origin = wall_origin or datetime.now(UTC)
        if self._wall_origin.tzinfo is None:
            raise ValueError("virtual clock wall origin must include a timezone")

    def monotonic(self) -> float:
        return self._monotonic

    def wall_time_utc(self) -> datetime:
        return self._wall_origin + timedelta(seconds=self._monotonic)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("virtual time cannot move backwards")
        self._monotonic += seconds
