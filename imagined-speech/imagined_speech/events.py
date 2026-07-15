"""Stable protocol-event data contract and event sinks."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from imagined_speech.config import Phase, StrictModel


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    SESSION_ABORTED = "session_aborted"
    SESSION_FAILED = "session_failed"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    TRIAL_REPEATED = "trial_repeated"
    BLOCK_REPEATED = "block_repeated"
    REFIT_RECORDED = "refit_recorded"
    REST_STARTED = "rest_started"
    REST_ENDED = "rest_ended"
    BLOCK_STARTED = "block_started"
    BLOCK_ENDED = "block_ended"
    BREAK_STARTED = "break_started"
    BREAK_ENDED = "break_ended"
    TRIAL_STARTED = "trial_started"
    TRIAL_ENDED = "trial_ended"
    PHASE_STARTED = "phase_started"
    PHASE_ENDED = "phase_ended"
    STIMULUS_PRESENTED = "stimulus_presented"


class EventSource(StrEnum):
    ENGINE = "engine"
    SYSTEM = "system"
    OPERATOR = "operator"
    SUBJECT_UI = "subject_ui"
    EXPERIMENTER_UI = "experimenter_ui"


class ProtocolEvent(StrictModel):
    schema_version: int = 1
    sequence_number: int = Field(ge=1)
    event_type: EventType
    marker_code: int = Field(gt=0)
    monotonic_seconds: float = Field(ge=0)
    wall_time_utc: datetime
    source: EventSource
    session_id: str
    plan_id: str
    block_id: str | None = None
    block_type: str | None = None
    block_number: int | None = None
    trial_id: str | None = None
    trial_number: int | None = None
    attempt: int | None = None
    phase: Phase | None = None
    stimulus_id: str | None = None
    stimulus_label: str | None = None
    step_id: str | None = None
    payload: dict[str, Any] = {}


class EventSink(Protocol):
    def emit(self, event: ProtocolEvent) -> None: ...


class NullEventSink:
    def emit(self, event: ProtocolEvent) -> None:
        del event


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[ProtocolEvent] = []

    def emit(self, event: ProtocolEvent) -> None:
        self.events.append(event)


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event: ProtocolEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)
