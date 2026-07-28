"""Pure monotonic-clock protocol runtime with frame acknowledgement."""

from __future__ import annotations

import uuid
from enum import StrEnum

from ssvep_bci.config.models import ExperimentConfig, HorizontalLayout, TargetSide
from ssvep_bci.events.bus import EventSink
from ssvep_bci.events.models import EventSource, EventType, ProtocolEvent
from ssvep_bci.planning.models import PlanStep, SessionPlan, StepKind
from ssvep_bci.runtime.clock import Clock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.view_state import ViewState
from ssvep_bci.stimuli.models import (
    StimulusNode,
    StimulusPlacementOverride,
    StimulusScene,
)


class RunState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    AWAITING_ONSET = "awaiting_onset"
    AWAITING_OFFSET = "awaiting_offset"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


TERMINAL_STATES = {RunState.COMPLETED, RunState.ABORTED, RunState.FAILED}


class ProtocolRuntime:
    def __init__(
        self,
        session_id: str,
        plan: SessionPlan,
        config: ExperimentConfig,
        clock: Clock,
        sink: EventSink,
    ) -> None:
        self.session_id = session_id
        self.plan = plan
        self.config = config
        self.clock = clock
        self.sink = sink
        self.state = RunState.READY
        self._cursor = 0
        self._deadline: float | None = None
        self._sequence = 0
        self._active_trial_id: str | None = None
        self._completed_trial_count = 0
        self._stimulus_onset: float | None = None
        self.error: str | None = None
        self._stimulus_indexes = {
            stimulus.id: index for index, stimulus in enumerate(config.stimuli)
        }
        self._stimuli_by_id = {stimulus.id: stimulus for stimulus in config.stimuli}

    @property
    def current_step(self) -> PlanStep | None:
        if self._cursor >= len(self.plan.steps):
            return None
        return self.plan.steps[self._cursor]

    @property
    def remaining_seconds(self) -> float:
        if self._deadline is None:
            return 0.0
        return max(0.0, self._deadline - self.clock.monotonic())

    def start(self) -> None:
        if self.state != RunState.READY:
            raise RuntimeError(f"cannot start protocol from {self.state.value}")
        self.state = RunState.RUNNING
        self._emit(EventType.SESSION_STARTED, marker_code=self.config.markers.session_start)
        self._enter_current(self.clock.monotonic())

    def tick(self) -> None:
        now = self.clock.monotonic()
        while (
            self.state == RunState.RUNNING
            and self._deadline is not None
            and now + 1e-9 >= self._deadline
        ):
            boundary = self._deadline
            step = self.current_step
            if step is None:
                return
            if step.kind == StepKind.STIMULUS:
                self._deadline = None
                self.state = RunState.AWAITING_OFFSET
                self._emit(EventType.OFFSET_REQUESTED, step=step)
                return
            self._cursor += 1
            self._deadline = None
            self._enter_current(boundary)

    def acknowledge_frame(self, command: PresentationFrameAcknowledged) -> None:
        step = self.current_step
        if step is None or step.presentation_id != command.presentation_id:
            raise ValueError("frame acknowledgement does not match current presentation")
        if command.kind == FrameKind.ONSET:
            if self.state != RunState.AWAITING_ONSET:
                raise RuntimeError("onset acknowledgement is not currently expected")
            self._stimulus_onset = command.monotonic_timestamp
            self.state = RunState.RUNNING
            self._deadline = command.monotonic_timestamp + step.duration_seconds
            self._emit(
                EventType.STIMULUS_ONSET,
                step=step,
                marker_code=self._stimulus_marker(step, onset=True),
                source=EventSource.SUBJECT_UI,
                occurrence=(command.monotonic_timestamp, command.wall_clock_timestamp_utc),
                payload={"confirmed_by_frame_swap": True},
            )
            return
        if self.state != RunState.AWAITING_OFFSET:
            raise RuntimeError("offset acknowledgement is not currently expected")
        self._emit(
            EventType.STIMULUS_OFFSET,
            step=step,
            marker_code=self._stimulus_marker(step, onset=False),
            source=EventSource.SUBJECT_UI,
            occurrence=(command.monotonic_timestamp, command.wall_clock_timestamp_utc),
            payload={
                "confirmed_by_frame_swap": True,
                "onset_monotonic_timestamp": self._stimulus_onset,
            },
        )
        self._emit(EventType.TRIAL_ENDED, step=step, marker_code=self.config.markers.trial_end)
        self._completed_trial_count += 1
        self._active_trial_id = None
        self._stimulus_onset = None
        self._cursor += 1
        self.state = RunState.RUNNING
        self._enter_current(command.monotonic_timestamp)

    def abort(self, reason: str, source: EventSource = EventSource.SUBJECT_UI) -> None:
        if self.state in TERMINAL_STATES:
            return
        step = self.current_step
        if step is not None and step.kind == StepKind.STIMULUS and self.state in {
            RunState.RUNNING, RunState.AWAITING_OFFSET
        }:
            self._emit(
                EventType.STIMULUS_OFFSET,
                step=step,
                marker_code=self._stimulus_marker(step, onset=False),
                source=source,
                payload={"confirmed_by_frame_swap": False, "reason": reason},
            )
        self.state = RunState.ABORTED
        self._deadline = None
        self._emit(
            EventType.SESSION_ABORTED,
            step=step,
            marker_code=self.config.markers.session_abort,
            source=source,
            payload={"reason": reason},
        )

    def fail(self, reason: str) -> None:
        if self.state in TERMINAL_STATES:
            return
        self.error = reason
        self.state = RunState.FAILED
        self._deadline = None
        self._emit(
            EventType.SESSION_FAILED,
            step=self.current_step,
            marker_code=self.config.markers.session_failed,
            source=EventSource.SYSTEM,
            payload={"reason": reason},
        )

    def view_state(self) -> ViewState:
        step = self.current_step
        target = (
            self._stimuli_by_id[step.stimulus_id]
            if step
            and step.kind in {StepKind.PRE_STIMULUS, StepKind.STIMULUS}
            and step.stimulus_id
            else None
        )
        scene = None
        if target is not None and step is not None:
            flashing = (
                step.kind == StepKind.STIMULUS
                and self.state in {RunState.AWAITING_ONSET, RunState.RUNNING}
            )
            dual = self.config.dual_stimulus
            multi = self.config.multi_stimulus
            if multi is not None and step.position_stimulus_ids is not None:
                scene_nodes = tuple(
                    StimulusNode(
                        stimulus=self._stimuli_by_id[stimulus_id],
                        visible_requested=True,
                        flashing_requested=flashing,
                        highlighted=(
                            step.kind == StepKind.PRE_STIMULUS
                            and stimulus_id == target.id
                        ),
                        placement_override=StimulusPlacementOverride(
                            width_px=multi.width_px,
                            height_px=multi.height_px,
                            center_x=position.center_x,
                            center_y=position.center_y,
                            horizontal_layout=HorizontalLayout.MANUAL,
                        ),
                    )
                    for position, stimulus_id in zip(
                        multi.positions,
                        step.position_stimulus_ids,
                        strict=True,
                    )
                )
            elif dual is not None and step.distractor_stimulus_id is not None:
                assert step.target_side is not None
                distractor_side = (
                    TargetSide.RIGHT
                    if step.target_side == TargetSide.LEFT
                    else TargetSide.LEFT
                )
                scene_nodes = (
                    StimulusNode(
                        stimulus=target,
                        visible_requested=True,
                        flashing_requested=flashing,
                        highlighted=step.kind == StepKind.PRE_STIMULUS,
                        placement_override=self._dual_placement(step.target_side),
                    ),
                    StimulusNode(
                        stimulus=self._stimuli_by_id[step.distractor_stimulus_id],
                        visible_requested=True,
                        flashing_requested=flashing,
                        highlighted=False,
                        placement_override=self._dual_placement(distractor_side),
                    ),
                )
            else:
                scene_ids = self.config.protocol.simultaneous_stimulus_ids or (
                    target.id,
                )
                scene_nodes = tuple(
                    StimulusNode(
                        stimulus=self._stimuli_by_id[stimulus_id],
                        visible_requested=True,
                        flashing_requested=flashing,
                        highlighted=(
                            step.kind == StepKind.PRE_STIMULUS
                            and stimulus_id == target.id
                        ),
                    )
                    for stimulus_id in scene_ids
                )
            scene = StimulusScene(
                scene_id=step.presentation_id or step.step_id,
                nodes=scene_nodes,
            )
        phase = step.kind.value if step else self.state.value
        messages = {
            StepKind.INITIAL_REST: "Prepare",
            StepKind.PRE_STIMULUS: "Focus on the stimulus location",
            StepKind.STIMULUS: "",
            StepKind.INTER_TRIAL: "Rest",
            StepKind.FINAL_REST: "Complete",
        }
        return ViewState(
            run_state=self.state.value,
            phase=phase,
            step_id=step.step_id if step else None,
            trial_number=step.trial_number if step else None,
            trial_count=self.plan.trial_count,
            completed_trial_count=self._completed_trial_count,
            presentation_id=step.presentation_id if step else None,
            scene=scene,
            remaining_seconds=self.remaining_seconds,
            message=messages.get(step.kind, self.state.value) if step else self.state.value,
            error=self.error,
        )

    def _enter_current(self, anchor: float) -> None:
        while self._cursor < len(self.plan.steps):
            step = self.plan.steps[self._cursor]
            if step.trial_id and step.trial_id != self._active_trial_id:
                self._active_trial_id = step.trial_id
                self._emit(
                    EventType.TRIAL_STARTED,
                    step=step,
                    marker_code=self.config.markers.trial_start,
                )
            if step.kind == StepKind.STIMULUS:
                self.state = RunState.AWAITING_ONSET
                self._emit(EventType.PRESENTATION_REQUESTED, step=step)
                return
            if step.duration_seconds <= 0:
                self._cursor += 1
                continue
            self.state = RunState.RUNNING
            self._deadline = anchor + step.duration_seconds
            return
        self.state = RunState.COMPLETED
        self._deadline = None
        self._emit(EventType.SESSION_COMPLETED, marker_code=self.config.markers.session_complete)

    def _stimulus_marker(self, step: PlanStep, *, onset: bool) -> int:
        assert step.stimulus_id is not None
        base = (
            self.config.markers.stimulus_onset_base
            if onset
            else self.config.markers.stimulus_offset_base
        )
        return base + self._stimulus_indexes[step.stimulus_id]

    def _dual_placement(self, side: TargetSide) -> StimulusPlacementOverride:
        dual = self.config.dual_stimulus
        assert dual is not None
        center_x = (
            dual.left_center_x if side == TargetSide.LEFT else dual.right_center_x
        )
        return StimulusPlacementOverride(
            side=side,
            width_px=dual.width_px,
            height_px=dual.height_px,
            center_y=dual.center_y,
            horizontal_layout=dual.horizontal_layout,
            center_x=center_x,
        )

    def _condition_payload(self, step: PlanStep | None) -> dict:
        if step is None or step.stimulus_id is None:
            return {}
        target_frequency = self._stimuli_by_id[step.stimulus_id].frequency_hz
        if (
            step.target_side is not None
            and step.distractor_stimulus_id is not None
        ):
            distractor_frequency = self._stimuli_by_id[
                step.distractor_stimulus_id
            ].frequency_hz
            return {
                "target_frequency_hz": target_frequency,
                "target_side": step.target_side.value,
                "distractor_frequency_hz": distractor_frequency,
                "distractor_stimulus_id": step.distractor_stimulus_id,
                "stimulus_frequencies_hz": [
                    target_frequency,
                    distractor_frequency,
                ],
            }
        multi = self.config.multi_stimulus
        if (
            multi is None
            or step.target_position_id is None
            or step.position_stimulus_ids is None
        ):
            return {}
        distractor_ids = [
            stimulus_id
            for stimulus_id in step.position_stimulus_ids
            if stimulus_id != step.stimulus_id
        ]
        distractor_frequencies = [
            self._stimuli_by_id[stimulus_id].frequency_hz
            for stimulus_id in distractor_ids
        ]
        return {
            "target_frequency_hz": target_frequency,
            "target_position_id": step.target_position_id,
            "distractor_frequencies_hz": distractor_frequencies,
            "distractor_stimulus_ids": distractor_ids,
            "stimulus_frequencies_hz": [
                self._stimuli_by_id[stimulus_id].frequency_hz
                for stimulus_id in step.position_stimulus_ids
            ],
            "stimulus_positions": {
                position.id: stimulus_id
                for position, stimulus_id in zip(
                    multi.positions,
                    step.position_stimulus_ids,
                    strict=True,
                )
            },
        }

    def _emit(
        self,
        event_type: EventType,
        *,
        step: PlanStep | None = None,
        marker_code: int | None = None,
        source: EventSource = EventSource.RUNTIME,
        occurrence=None,
        payload: dict | None = None,
    ) -> None:
        self._sequence += 1
        monotonic, wall = occurrence or (self.clock.monotonic(), self.clock.wall_time_utc())
        event_payload = self._condition_payload(step)
        event_payload.update(payload or {})
        self.sink.emit(ProtocolEvent(
            event_id=str(uuid.uuid4()),
            sequence_number=self._sequence,
            event_type=event_type,
            marker_code=marker_code,
            monotonic_timestamp=monotonic,
            wall_clock_timestamp_utc=wall,
            source=source,
            session_id=self.session_id,
            plan_id=self.plan.plan_id,
            step_id=step.step_id if step else None,
            trial_id=step.trial_id if step else None,
            trial_number=step.trial_number if step else None,
            stimulus_id=step.stimulus_id if step else None,
            presentation_id=step.presentation_id if step else None,
            payload=event_payload,
        ))
