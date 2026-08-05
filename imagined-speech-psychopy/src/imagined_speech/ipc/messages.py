"""Strict version-2 JSONL message contracts for local process communication."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from imagined_speech.config import StrictModel, SubjectWindowMode
from imagined_speech.events import ProtocolEvent
from imagined_speech.runtime.commands import OperatorCommandRecord


PROTOCOL_VERSION = 2


class ClientRole(StrEnum):
    OPERATOR = "operator"
    SUBJECT = "subject"


class MessageType(StrEnum):
    HELLO = "hello"
    HELLO_ACCEPTED = "hello_accepted"
    SERVICE_STATE = "service_state"
    CREATE_SESSION = "create_session"
    SESSION_READY = "session_ready"
    OPERATOR_COMMAND = "operator_command"
    OPERATOR_COMMAND_RESULT = "operator_command_result"
    OPERATOR_STATE = "operator_state"
    SUBJECT_INIT = "subject_init"
    PRESENTATION_REQUEST = "presentation_request"
    PRESENTATION_INTERRUPT = "presentation_interrupt"
    FRAME_TIMING = "frame_timing"
    FRAME_ACK = "frame_ack"
    TIMING_PREFLIGHT = "timing_preflight"
    SUBJECT_ABORT = "subject_abort"
    SESSION_FINALIZED = "session_finalized"
    CLOCK_PING = "clock_ping"
    CLOCK_PONG = "clock_pong"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class Envelope(StrictModel):
    protocol_version: Literal[2] = PROTOCOL_VERSION
    type: MessageType
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    revision: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class HelloPayload(StrictModel):
    role: ClientRole
    software_version: str
    process_id: int = Field(gt=0)


class SubjectDisplayTargetPayload(StrictModel):
    device_name: str | None = None
    psychopy_index: int = Field(ge=0)
    qt_index: int = Field(ge=0)
    qt_name: str = ""
    geometry: tuple[int, int, int, int]
    primary: bool = False


class CreateSessionPayload(StrictModel):
    config_path: str
    participant_id: str
    session_label: str | None = None
    output_root: str | None = None
    auto_start: bool = False
    screen_index: int | None = Field(default=None, ge=0)
    subject_display: SubjectDisplayTargetPayload | None = None
    window_mode: SubjectWindowMode | None = None
    device_profile_path: str | None = None
    random_seed: int | None = Field(default=None, ge=0)


class OperatorCommandPayload(StrictModel):
    command: str
    note: str | None = None


class ServiceStatePayload(StrictModel):
    subject_connected: bool
    active_session_id: str | None = None
    active_runtime_state: str | None = None
    can_create_session: bool


class SessionReadyPayload(StrictModel):
    session_path: str
    participant_id: str


class OperatorViewStatePayload(StrictModel):
    run_state: str
    screen: str
    step_id: str | None
    headline: str
    instruction: str
    stimulus_id: str | None
    stimulus_label: str | None
    remaining_seconds: float
    duration_seconds: float
    block_type: str | None
    block_number: int | None
    block_count: int | None
    trial_number: int | None
    trial_count: int | None
    presentation_id: str | None = None
    revision: int = Field(default=0, ge=0)


class AcquisitionStatePayload(StrictModel):
    running: bool
    sample_count: int = Field(ge=0)
    dropped_batches: int = Field(ge=0)
    dropped_samples: int = Field(ge=0)
    timestamp_discontinuities: int = Field(ge=0)
    read_errors: int = Field(ge=0)
    write_errors: int = Field(ge=0)
    last_health_kind: str
    last_health_severity: str
    channel_names: tuple[str, ...]
    recent_samples: tuple[tuple[float, ...], ...]
    sampling_rate_hz: float = Field(gt=0)
    eeg_channel_indexes: tuple[int, ...]
    eeg_channel_labels: tuple[str, ...]
    raw_file_size_bytes: int = Field(ge=0)
    free_storage_bytes: int = Field(ge=0)


class OperatorStatePayload(StrictModel):
    runtime_state: str
    engine_state: str
    protocol_started: bool
    recording: bool
    session_path: str
    session_id: str
    participant_id: str
    session_label: str | None
    experiment_id: str
    device_profile_id: str
    view_state: OperatorViewStatePayload
    acquisition: AcquisitionStatePayload
    events: tuple[ProtocolEvent, ...] = ()
    operator_records: tuple[OperatorCommandRecord, ...] = ()
    timing_warnings: tuple[str, ...] = ()
    error: str | None = None
    engine_diagnostics: dict[str, Any] = Field(default_factory=dict)


class SessionValidationPayload(StrictModel):
    session_path: str
    session_id: str
    status: str
    event_count: int = Field(ge=0)
    trial_count: int = Field(ge=0)
    phase_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    warnings: tuple[str, ...] = ()
    operator_command_count: int = Field(ge=0)


class SessionFinalizedPayload(StrictModel):
    state: str
    session_path: str
    session_id: str
    participant_id: str
    session_label: str | None
    experiment_id: str
    device_profile_id: str
    timing_warnings: tuple[str, ...] = ()
    error: str | None = None
    engine: dict[str, Any] = Field(default_factory=dict)
    validation: SessionValidationPayload | None = None


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


class FrameTimingPayload(StrictModel):
    presentation_id: str = Field(min_length=1)
    subject_monotonic_ns: int = Field(ge=0)
    wall_time_utc: datetime
    frame_index: int = Field(ge=0)
    dropped_frames: int = Field(default=0, ge=0)
    frame_intervals_seconds: tuple[float, ...] = Field(min_length=1)


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
