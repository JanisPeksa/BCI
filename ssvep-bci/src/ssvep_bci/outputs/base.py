"""Side-effect boundary for future command and text output modules."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from ssvep_bci.config.models import StrictModel


class SelectionDecision(StrictModel):
    schema_version: int = 1
    session_id: str = Field(min_length=1)
    presentation_id: str = Field(min_length=1)
    predicted_index: int = Field(ge=0)
    predicted_frequency_hz: float = Field(gt=0)
    scores: tuple[float, ...]


class OutputSink(Protocol):
    def emit(self, decision: SelectionDecision) -> None: ...

    def close(self) -> None: ...
