from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class FrameKind(StrEnum):
    ONSET = "onset"
    OFFSET = "offset"


@dataclass(frozen=True)
class PresentationFrameAcknowledged:
    presentation_id: str
    kind: FrameKind
    monotonic_timestamp: float
    wall_clock_timestamp_utc: datetime


@dataclass(frozen=True)
class AbortSession:
    reason: str = "subject requested abort"


@dataclass(frozen=True)
class CloseRequested:
    reason: str = "subject window closed"


RuntimeCommand = PresentationFrameAcknowledged | AbortSession | CloseRequested

