from __future__ import annotations

from psychopy_ssvep.config.models import StrictModel
from psychopy_ssvep.stimuli.models import StimulusScene


class ViewState(StrictModel):
    run_state: str
    phase: str
    step_id: str | None = None
    trial_number: int | None = None
    trial_count: int | None = None
    completed_trial_count: int = 0
    presentation_id: str | None = None
    scene: StimulusScene | None = None
    remaining_seconds: float = 0.0
    message: str = ""
    error: str | None = None
