"""Typed operator, presentation, and lifecycle commands."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from imagined_speech.config import StrictModel


class OperatorCommand(StrEnum):
    START_PROTOCOL = "start_protocol"
    PAUSE = "pause"
    RESUME = "resume"
    REPEAT_TRIAL = "repeat_trial"
    REPEAT_BLOCK = "repeat_block"
    REFIT = "refit"
    ABORT = "abort"


class OperatorCommandStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class OperatorCommandRecord(StrictModel):
    schema_version: Literal[1, 2] = 2
    record_type: Literal["operator_command"] = "operator_command"
    sequence_number: int
    command: OperatorCommand
    status: OperatorCommandStatus
    source: str
    monotonic_seconds: float
    wall_time_utc: datetime
    reason: str
    note: str | None = None
    state_before: str
    resulting_state: str
    session_id: str
    block_id: str | None = None
    trial_id: str | None = None
    attempt: int | None = None
    payload: dict[str, Any] = {}
