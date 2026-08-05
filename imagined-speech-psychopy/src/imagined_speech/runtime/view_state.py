"""Presentation-safe runtime state shared with subject and operator clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from imagined_speech.runtime.protocol import RunState


@dataclass(frozen=True)
class ViewState:
    run_state: RunState
    screen: str
    step_id: str | None
    headline: str
    instruction: str
    stimulus_id: str | None
    stimulus_label: str | None
    remaining_seconds: float
    duration_seconds: float
    stage_type: Literal["practice", "experiment"] | None
    block_number: int | None
    block_count: int | None
    trial_number: int | None
    trial_count: int | None
    presentation_id: str | None = None
    revision: int = 0
