from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from ssvep_bci.config.models import StrictModel


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    SESSION_ABORTED = "session_aborted"
    SESSION_FAILED = "session_failed"
    TRIAL_STARTED = "trial_started"
    TRIAL_ENDED = "trial_ended"
    PRESENTATION_REQUESTED = "presentation_requested"
    STIMULUS_ONSET = "stimulus_onset"
    OFFSET_REQUESTED = "offset_requested"
    STIMULUS_OFFSET = "stimulus_offset"


class EventSource(StrEnum):
    RUNTIME = "runtime"
    SUBJECT_UI = "subject_ui"
    SYSTEM = "system"


class ProtocolEvent(StrictModel):
    schema_version: int = 1
    event_id: str
    sequence_number: int = Field(ge=1)
    event_type: EventType
    marker_code: int | None = Field(default=None, gt=0)
    monotonic_timestamp: float = Field(ge=0)
    wall_clock_timestamp_utc: datetime
    source: EventSource
    session_id: str
    plan_id: str
    step_id: str | None = None
    trial_id: str | None = None
    trial_number: int | None = None
    stimulus_id: str | None = None
    presentation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

