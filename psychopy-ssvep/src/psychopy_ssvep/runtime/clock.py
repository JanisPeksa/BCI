from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def wall_time_utc(self) -> datetime: ...


class RealClock:
    def monotonic(self) -> float:
        return time.perf_counter()

    def wall_time_utc(self) -> datetime:
        return datetime.now(UTC)


class VirtualClock:
    def __init__(self, wall_origin: datetime | None = None) -> None:
        self._value = 0.0
        self._origin = wall_origin or datetime(2026, 1, 1, tzinfo=UTC)
        if self._origin.tzinfo is None:
            raise ValueError("wall_origin must be timezone aware")

    def monotonic(self) -> float:
        return self._value

    def wall_time_utc(self) -> datetime:
        return self._origin + timedelta(seconds=self._value)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("virtual time cannot move backwards")
        self._value += seconds

