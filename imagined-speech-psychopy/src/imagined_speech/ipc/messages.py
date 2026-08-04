"""Strict version-1 JSONL message contracts for local process communication."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from imagined_speech.config import StrictModel, SubjectWindowMode


PROTOCOL_VERSION = 1


class ClientRole(StrEnum):
    OPERATOR = "operator"
    SUBJECT = "subject"


class MessageType(StrEnum):
    HELLO = "hello"
    HELLO_ACCEPTED = "hello_accepted"
    CREATE_SESSION = "create_session"
    SESSION_READY = "session_ready"
    OPERATOR_COMMAND = "operator_command"
    OPERATOR_COMMAND_RESULT = "operator_command_result"
    OPERATOR_STATE = "operator_state"
    SUBJECT_INIT = "subject_init"
    PRESENTATION_REQUEST = "presentation_request"
    PRESENTATION_INTERRUPT = "presentation_interrupt"
    FRAME_ACK = "frame_ack"
    TIMING_PREFLIGHT = "timing_preflight"
    SUBJECT_ABORT = "subject_abort"
    SESSION_FINALIZED = "session_finalized"
    CLOCK_PING = "clock_ping"
    CLOCK_PONG = "clock_pong"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class Envelope(StrictModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    type: MessageType
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    revision: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class HelloPayload(StrictModel):
    role: ClientRole
    software_version: str
    process_id: int = Field(gt=0)


class CreateSessionPayload(StrictModel):
    config_path: str
    participant_id: str
    session_label: str | None = None
    output_root: str | None = None
    auto_start: bool = False
    screen_index: int | None = Field(default=None, ge=0)
    window_mode: SubjectWindowMode | None = None


class OperatorCommandPayload(StrictModel):
    command: str
    note: str | None = None


class TimingPreflightPayload(StrictModel):
    passed: bool
    measured_refresh_rate_hz: float = Field(ge=0)
    dropped_frame_fraction: float = Field(ge=0, le=1)
    frame_interval_count: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    frame_intervals_seconds: tuple[float, ...] = ()
    error: str | None = None


class FrameAcknowledgementPayload(StrictModel):
    previous_presentation_id: str | None = None
    presentation_id: str | None = None
    neutral: bool = False
    flip_time: float | None = None
    subject_monotonic_ns: int = Field(ge=0)
    wall_time_utc: datetime
    frame_index: int = Field(ge=0)
    dropped_frames: int = Field(default=0, ge=0)
    frame_interval_seconds: float | None = Field(default=None, gt=0)
    frame_intervals_seconds: tuple[float, ...] = ()
    audio_scheduled_time: float | None = None
    audio_started: bool = False


class SubjectAbortPayload(StrictModel):
    reason: str = Field(min_length=1)
    acknowledgement: FrameAcknowledgementPayload


class ClockPingPayload(StrictModel):
    sequence: int = Field(ge=0)
    backend_send_monotonic_ns: int = Field(ge=0)


class ClockPongPayload(ClockPingPayload):
    subject_receive_monotonic_ns: int = Field(ge=0)
    subject_send_monotonic_ns: int = Field(ge=0)


def message(
    type_: MessageType,
    payload: StrictModel | dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    revision: int = 0,
) -> Envelope:
    if isinstance(payload, StrictModel):
        value = payload.model_dump(mode="json")
    else:
        value = payload or {}
    return Envelope(type=type_, session_id=session_id, revision=revision, payload=value)
