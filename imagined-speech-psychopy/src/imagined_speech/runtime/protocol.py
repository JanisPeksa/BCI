"""Presentation-gated protocol state machine shared by all front ends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from imagined_speech.config import ExperimentConfig, MarkerConfig, Phase
from imagined_speech.events import (
    EventSink,
    EventSource,
    EventType,
    NullEventSink,
    ProtocolEvent,
)
from imagined_speech.planning import BlockPlan, BreakPlan, RestPlan, SessionPlan, TrialPlan
from imagined_speech.runtime.clock import ProtocolClock
from imagined_speech.runtime.view_state import ViewState


class RunState(StrEnum):
    READY = "ready"
    AWAITING_PRESENTATION = "awaiting_presentation"
    AWAITING_NEUTRAL = "awaiting_neutral"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


TERMINAL_STATES = {RunState.COMPLETED, RunState.ABORTED, RunState.FAILED}


@dataclass(frozen=True)
class _Context:
    step_id: str | None = None
    block_id: str | None = None
    block_type: str | None = None
    block_number: int | None = None
    block_count: int | None = None
    trial_id: str | None = None
    trial_number: int | None = None
    trial_count: int | None = None
    attempt: int | None = None
    phase: Phase | None = None
    stimulus_id: str | None = None
    stimulus_label: str | None = None
    instruction: str = ""
    scope: str | None = None


@dataclass(frozen=True)
class _EventAction:
    event_type: EventType
    context: _Context


@dataclass(frozen=True)
class _TimedAction:
    screen: str
    duration_seconds: float
    context: _Context


RuntimeAction = _EventAction | _TimedAction


def _headline(screen: str, stimulus_label: str | None = None) -> str:
    if screen == Phase.STIMULUS.value:
        return stimulus_label or "STIMULUS"
    return {
        Phase.REST.value: "REST",
        Phase.THINKING.value: "THINKING",
        Phase.PAUSE.value: "PAUSE",
        Phase.SPEAKING.value: "SPEAKING",
        "break": "BREAK",
    }.get(screen, screen.upper())


def _trial_runtime_actions(
    block: BlockPlan,
    trial: TrialPlan,
    trial_count: int,
    attempt: int,
) -> list[RuntimeAction]:
    actions: list[RuntimeAction] = []
    trial_context = _Context(
        step_id=trial.trial_id,
        block_id=block.block_id,
        block_type=block.block_type,
        block_number=block.block_number,
        block_count=block.block_count,
        trial_id=trial.trial_id,
        trial_number=trial.trial_number,
        trial_count=trial_count,
        attempt=attempt,
        stimulus_id=trial.stimulus_id,
        stimulus_label=trial.stimulus_label,
    )
    actions.append(_EventAction(EventType.TRIAL_STARTED, trial_context))
    for phase in trial.phases:
        phase_context = _Context(
            step_id=phase.step_id,
            block_id=block.block_id,
            block_type=block.block_type,
            block_number=block.block_number,
            block_count=block.block_count,
            trial_id=trial.trial_id,
            trial_number=trial.trial_number,
            trial_count=trial_count,
            attempt=attempt,
            phase=phase.phase,
            stimulus_id=trial.stimulus_id,
            stimulus_label=trial.stimulus_label,
            instruction=phase.instruction,
        )
        actions.append(_EventAction(EventType.PHASE_STARTED, phase_context))
        if phase.phase == Phase.STIMULUS:
            actions.append(_EventAction(EventType.STIMULUS_PRESENTED, phase_context))
        actions.append(
            _TimedAction(
                screen=phase.phase.value,
                duration_seconds=phase.duration_seconds,
                context=phase_context,
            )
        )
        actions.append(_EventAction(EventType.PHASE_ENDED, phase_context))
    actions.append(_EventAction(EventType.TRIAL_ENDED, trial_context))
    return actions


def _block_context(block: BlockPlan) -> _Context:
    return _Context(
        step_id=block.block_id,
        block_id=block.block_id,
        block_type=block.block_type,
        block_number=block.block_number,
        block_count=block.block_count,
    )


def build_runtime_actions(plan: SessionPlan) -> tuple[RuntimeAction, ...]:
    actions: list[RuntimeAction] = []
    practice_total = plan.practice_trial_count
    experiment_total = plan.experiment_trial_count

    for item in plan.items:
        if isinstance(item, RestPlan):
            context = _Context(
                step_id=item.rest_id,
                instruction=item.instruction,
                scope=item.rest_type,
            )
            actions.extend(
                (
                    _EventAction(EventType.REST_STARTED, context),
                    _TimedAction(Phase.REST.value, item.duration_seconds, context),
                    _EventAction(EventType.REST_ENDED, context),
                )
            )
            continue

        if isinstance(item, BreakPlan):
            context = _Context(
                step_id=item.break_id,
                block_number=item.after_block,
                instruction=item.instruction,
                scope="inter_block",
            )
            actions.extend(
                (
                    _EventAction(EventType.BREAK_STARTED, context),
                    _TimedAction("break", item.duration_seconds, context),
                    _EventAction(EventType.BREAK_ENDED, context),
                )
            )
            continue

        assert isinstance(item, BlockPlan)
        block_context = _block_context(item)
        actions.append(_EventAction(EventType.BLOCK_STARTED, block_context))
        trial_count = practice_total if item.block_type == "practice" else experiment_total
        for trial in item.trials:
            actions.extend(_trial_runtime_actions(item, trial, trial_count, attempt=1))
        actions.append(_EventAction(EventType.BLOCK_ENDED, block_context))
    return tuple(actions)


class ProtocolEngine:
    def __init__(
        self,
        session_id: str,
        plan: SessionPlan,
        config: ExperimentConfig,
        clock: ProtocolClock,
        sink: EventSink | None = None,
    ) -> None:
        self.session_id = session_id
        self.plan = plan
        self.config = config
        self.clock = clock
        self.sink = sink or NullEventSink()
        self.state = RunState.READY
        self._actions = list(build_runtime_actions(plan))
        self._cursor = 0
        self._current: _TimedAction | None = None
        self._deadline: float | None = None
        self._paused_remaining = 0.0
        self._sequence = 0
        self._stimulus_indexes = {
            stimulus.id: index for index, stimulus in enumerate(config.stimuli)
        }
        self._blocks = {block.block_id: block for block in plan.blocks}
        self._trials = {
            trial.trial_id: trial for block in plan.blocks for trial in block.trials
        }
        self._attempts: dict[str, int] = {}
        self.failure_reason: str | None = None

    @property
    def current_action(self) -> _TimedAction | None:
        return self._current

    @property
    def remaining_seconds(self) -> float:
        if self.state == RunState.PAUSED:
            return self._paused_remaining
        if self.state != RunState.RUNNING or self._deadline is None:
            return 0.0
        return max(0.0, self._deadline - self.clock.monotonic())

    def diagnostic_state(self) -> dict[str, object]:
        """Return persistence-safe protocol internals for failure diagnostics."""
        context = self._current.context if self._current is not None else None
        return {
            "engine_state": self.state.value,
            "failure_reason": self.failure_reason,
            "cursor": self._cursor,
            "action_count": len(self._actions),
            "deadline_monotonic_seconds": self._deadline,
            "remaining_seconds": self.remaining_seconds,
            "paused_remaining_seconds": self._paused_remaining,
            "current_screen": self._current.screen if self._current else None,
            "current_step_id": context.step_id if context else None,
            "block_id": context.block_id if context else None,
            "trial_id": context.trial_id if context else None,
            "attempt": context.attempt if context else None,
        }

    def start(self) -> None:
        if self.state != RunState.READY:
            raise RuntimeError(f"cannot start protocol from {self.state.value}")
        self.state = RunState.RUNNING
        self._emit(EventType.SESSION_STARTED, _Context(), EventSource.SYSTEM)
        self._advance_to_timed(self.clock.monotonic())

    def tick(self) -> None:
        if self.state != RunState.RUNNING:
            return
        now = self.clock.monotonic()
        while (
            self.state == RunState.RUNNING
            and self._current is not None
            and self._deadline is not None
            and now + 1e-9 >= self._deadline
        ):
            boundary = self._deadline
            self._current = None
            self._deadline = None
            self._advance_to_timed(boundary)

    def pause(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state != RunState.RUNNING or self._current is None:
            raise RuntimeError(f"cannot pause protocol from {self.state.value}")
        self._paused_remaining = self.remaining_seconds
        self.state = RunState.PAUSED
        self._emit(EventType.SESSION_PAUSED, self._current.context, source)

    def resume(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state != RunState.PAUSED or self._current is None:
            raise RuntimeError(f"cannot resume protocol from {self.state.value}")
        self._deadline = self.clock.monotonic() + self._paused_remaining
        self._paused_remaining = 0.0
        self.state = RunState.RUNNING
        self._emit(EventType.SESSION_RESUMED, self._current.context, source)

    def repeat_current_trial(
        self, source: EventSource = EventSource.OPERATOR
    ) -> None:
        context = self._require_active_trial("repeat trial")
        assert context.trial_id is not None and context.block_id is not None
        was_paused = self.state == RunState.PAUSED
        reuse_unpresented_attempt = self._current_attempt_is_unpresented()
        previous_attempt = context.attempt or self._attempts.get(context.trial_id, 1)
        self._close_current_trial("superseded")
        self._discard_until(
            lambda action: isinstance(action, _EventAction)
            and action.event_type == EventType.TRIAL_ENDED
            and action.context.trial_id == context.trial_id
        )

        block = self._blocks[context.block_id]
        trial = self._trials[context.trial_id]
        next_attempt = (
            previous_attempt
            if reuse_unpresented_attempt
            else max(self._attempts.get(context.trial_id, 0), previous_attempt) + 1
        )
        trial_count = (
            self.plan.practice_trial_count
            if block.block_type == "practice"
            else self.plan.experiment_trial_count
        )
        self._actions[self._cursor:self._cursor] = _trial_runtime_actions(
            block, trial, trial_count, next_attempt
        )
        repeat_context = self._trial_context(block, trial, next_attempt)
        self._emit(
            EventType.TRIAL_REPEATED,
            repeat_context,
            source,
            payload={
                "superseded_attempt": previous_attempt,
                "new_attempt": next_attempt,
                "superseded_before_onset": reuse_unpresented_attempt,
            },
        )
        self._restart_after_recovery(was_paused)

    def repeat_current_block(
        self, source: EventSource = EventSource.OPERATOR
    ) -> None:
        context = self._require_active_trial("repeat block")
        assert context.block_id is not None
        was_paused = self.state == RunState.PAUSED
        block = self._blocks[context.block_id]
        superseded_attempts = {
            trial.trial_id: self._attempts[trial.trial_id]
            for trial in block.trials
            if trial.trial_id in self._attempts
        }
        self._close_current_trial("superseded")
        self._discard_until(
            lambda action: isinstance(action, _EventAction)
            and action.event_type == EventType.BLOCK_ENDED
            and action.context.block_id == block.block_id
        )

        trial_count = (
            self.plan.practice_trial_count
            if block.block_type == "practice"
            else self.plan.experiment_trial_count
        )
        repeated_actions: list[RuntimeAction] = []
        for trial in block.trials:
            next_attempt = self._attempts.get(trial.trial_id, 0) + 1
            repeated_actions.extend(
                _trial_runtime_actions(block, trial, trial_count, next_attempt)
            )
        repeated_actions.append(_EventAction(EventType.BLOCK_ENDED, _block_context(block)))
        self._actions[self._cursor:self._cursor] = repeated_actions
        self._emit(
            EventType.BLOCK_REPEATED,
            _block_context(block),
            source,
            payload={"superseded_attempts": superseded_attempts},
        )
        self._restart_after_recovery(was_paused)

    def record_refit(
        self, note: str, source: EventSource = EventSource.OPERATOR
    ) -> None:
        if self.state not in {RunState.RUNNING, RunState.PAUSED}:
            raise RuntimeError(f"cannot record refit from {self.state.value}")
        cleaned = note.strip()
        if not cleaned:
            raise ValueError("refit note must not be empty")
        context = self._current.context if self._current else _Context()
        self._emit(
            EventType.REFIT_RECORDED,
            context,
            source,
            payload={"note": cleaned},
        )

    def abort(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state not in {RunState.READY, RunState.RUNNING, RunState.PAUSED}:
            raise RuntimeError(f"cannot abort protocol from {self.state.value}")
        context = self._current.context if self._current else _Context()
        if self.state in {RunState.RUNNING, RunState.PAUSED}:
            self._close_current_scopes("aborted")
        protocol_started = self.state != RunState.READY
        self.state = RunState.ABORTED
        self._emit(
            EventType.SESSION_ABORTED,
            context,
            source,
            payload={"protocol_started": protocol_started},
        )

    def fail(self, reason: str) -> None:
        if self.state in TERMINAL_STATES:
            return
        self.failure_reason = reason
        context = self._current.context if self._current else _Context()
        self.state = RunState.FAILED
        self._emit(
            EventType.SESSION_FAILED,
            context,
            EventSource.SYSTEM,
            payload={"reason": reason},
        )

    def view_state(self) -> ViewState:
        if self.state == RunState.READY:
            return ViewState(
                self.state, "ready", None, "READY", "", None, None, 0, 0,
                None, None, None, None, None,
            )
        if self.state == RunState.PAUSED:
            context = self._current.context if self._current else _Context()
            return ViewState(
                self.state,
                "paused",
                context.step_id,
                "PAUSED",
                "Please wait for the researcher.",
                None,
                None,
                self._paused_remaining,
                self._current.duration_seconds if self._current else 0,
                context.block_type,
                context.block_number,
                context.block_count,
                context.trial_number,
                context.trial_count,
            )
        if self.state in TERMINAL_STATES:
            headline = {
                RunState.COMPLETED: "COMPLETE",
                RunState.ABORTED: "SESSION ENDED",
                RunState.FAILED: "SESSION ERROR",
            }[self.state]
            return ViewState(
                self.state, self.state.value, None, headline, "", None, None,
                0, 0, None, None, None, None, None,
            )

        assert self._current is not None
        context = self._current.context
        return ViewState(
            run_state=self.state,
            screen=self._current.screen,
            step_id=context.step_id,
            headline=_headline(self._current.screen, context.stimulus_label),
            instruction=context.instruction,
            stimulus_id=context.stimulus_id,
            stimulus_label=context.stimulus_label,
            remaining_seconds=self.remaining_seconds,
            duration_seconds=self._current.duration_seconds,
            block_type=context.block_type,
            block_number=context.block_number,
            block_count=context.block_count,
            trial_number=context.trial_number,
            trial_count=context.trial_count,
        )

    def _advance_to_timed(self, anchor: float) -> None:
        while self._cursor < len(self._actions):
            action = self._actions[self._cursor]
            self._cursor += 1
            if isinstance(action, _EventAction):
                payload = None
                if action.event_type == EventType.TRIAL_STARTED:
                    assert action.context.trial_id is not None
                    self._attempts[action.context.trial_id] = action.context.attempt or 1
                elif action.event_type == EventType.TRIAL_ENDED:
                    payload = {"outcome": "completed"}
                self._emit(
                    action.event_type,
                    action.context,
                    EventSource.ENGINE,
                    payload=payload,
                )
                continue
            self._current = action
            self._deadline = anchor + action.duration_seconds
            return

        self.state = RunState.COMPLETED
        self._emit(EventType.SESSION_COMPLETED, _Context(), EventSource.SYSTEM)

    def _require_active_trial(self, operation: str) -> _Context:
        if self.state not in {RunState.RUNNING, RunState.PAUSED}:
            raise RuntimeError(f"cannot {operation} from {self.state.value}")
        if self._current is None or self._current.context.trial_id is None:
            raise RuntimeError(f"cannot {operation} outside a trial")
        return self._current.context

    def _current_attempt_is_unpresented(self) -> bool:
        return False

    def _trial_context(
        self, block: BlockPlan, trial: TrialPlan, attempt: int
    ) -> _Context:
        trial_count = (
            self.plan.practice_trial_count
            if block.block_type == "practice"
            else self.plan.experiment_trial_count
        )
        return _Context(
            step_id=trial.trial_id,
            block_id=block.block_id,
            block_type=block.block_type,
            block_number=block.block_number,
            block_count=block.block_count,
            trial_id=trial.trial_id,
            trial_number=trial.trial_number,
            trial_count=trial_count,
            attempt=attempt,
            stimulus_id=trial.stimulus_id,
            stimulus_label=trial.stimulus_label,
        )

    def _close_current_trial(self, outcome: str) -> None:
        assert self._current is not None
        context = self._current.context
        if context.phase is not None:
            self._emit(
                EventType.PHASE_ENDED,
                context,
                EventSource.ENGINE,
                payload={"outcome": outcome},
            )
        if context.trial_id is not None and context.block_id is not None:
            block = self._blocks[context.block_id]
            trial = self._trials[context.trial_id]
            self._emit(
                EventType.TRIAL_ENDED,
                self._trial_context(block, trial, context.attempt or 1),
                EventSource.ENGINE,
                payload={"outcome": outcome},
            )
        self._current = None
        self._deadline = None
        self._paused_remaining = 0.0

    def _close_current_scopes(self, outcome: str) -> None:
        if self._current is None:
            return
        context = self._current.context
        if context.trial_id is not None:
            self._close_current_trial(outcome)
            if context.block_id is not None:
                self._emit(
                    EventType.BLOCK_ENDED,
                    _block_context(self._blocks[context.block_id]),
                    EventSource.ENGINE,
                    payload={"outcome": outcome},
                )
            return
        if context.scope in {"initial", "final"}:
            self._emit(
                EventType.REST_ENDED,
                context,
                EventSource.ENGINE,
                payload={"outcome": outcome},
            )
        elif context.scope == "inter_block":
            self._emit(
                EventType.BREAK_ENDED,
                context,
                EventSource.ENGINE,
                payload={"outcome": outcome},
            )
        self._current = None
        self._deadline = None
        self._paused_remaining = 0.0

    def _discard_until(self, predicate) -> None:
        for index in range(self._cursor, len(self._actions)):
            if predicate(self._actions[index]):
                del self._actions[self._cursor:index + 1]
                return
        raise RuntimeError("cannot locate the end of the active recovery scope")

    def _restart_after_recovery(self, remain_paused: bool) -> None:
        self.state = RunState.RUNNING
        self._advance_to_timed(self.clock.monotonic())
        if remain_paused and self._current is not None and self.state == RunState.RUNNING:
            self._paused_remaining = self._current.duration_seconds
            self._deadline = None
            self.state = RunState.PAUSED

    def _marker_code(self, event_type: EventType, context: _Context) -> int:
        markers: MarkerConfig = self.config.markers
        direct = {
            EventType.SESSION_STARTED: markers.session_start,
            EventType.SESSION_COMPLETED: markers.session_complete,
            EventType.SESSION_ABORTED: markers.session_abort,
            EventType.SESSION_FAILED: markers.session_abort,
            EventType.SESSION_PAUSED: markers.operator_pause,
            EventType.SESSION_RESUMED: markers.operator_resume,
            EventType.TRIAL_REPEATED: markers.operator_repeat_trial,
            EventType.BLOCK_REPEATED: markers.operator_repeat_block,
            EventType.REFIT_RECORDED: markers.operator_refit,
            EventType.BLOCK_STARTED: markers.block_start,
            EventType.BLOCK_ENDED: markers.block_end,
            EventType.BREAK_STARTED: markers.break_start,
            EventType.BREAK_ENDED: markers.break_end,
            EventType.TRIAL_STARTED: markers.trial_start,
            EventType.TRIAL_ENDED: markers.trial_end,
            EventType.REST_STARTED: markers.phase_start[Phase.REST],
            EventType.REST_ENDED: markers.phase_end[Phase.REST],
        }
        if event_type in direct:
            return direct[event_type]
        if event_type == EventType.PHASE_STARTED:
            assert context.phase is not None
            return markers.phase_start[context.phase]
        if event_type == EventType.PHASE_ENDED:
            assert context.phase is not None
            return markers.phase_end[context.phase]
        if event_type == EventType.STIMULUS_PRESENTED:
            assert context.stimulus_id is not None
            return markers.stimulus_base + self._stimulus_indexes[context.stimulus_id]
        raise AssertionError(f"no marker mapping for {event_type.value}")

    def _emit(
        self,
        event_type: EventType,
        context: _Context,
        source: EventSource,
        payload: dict[str, object] | None = None,
        occurrence: tuple[float, datetime] | None = None,
    ) -> None:
        self._sequence += 1
        event_payload: dict[str, object] = {}
        if context.scope is not None:
            event_payload["scope"] = context.scope
        if payload:
            event_payload.update(payload)
        monotonic_seconds, wall_time_utc = occurrence or (
            self.clock.monotonic(),
            self.clock.wall_time_utc(),
        )
        self.sink.emit(
            ProtocolEvent(
                sequence_number=self._sequence,
                event_type=event_type,
                marker_code=self._marker_code(event_type, context),
                monotonic_seconds=monotonic_seconds,
                wall_time_utc=wall_time_utc,
                source=source,
                session_id=self.session_id,
                plan_id=self.plan.plan_id,
                block_id=context.block_id,
                block_type=context.block_type,
                block_number=context.block_number,
                trial_id=context.trial_id,
                trial_number=context.trial_number,
                attempt=context.attempt,
                phase=context.phase,
                stimulus_id=context.stimulus_id,
                stimulus_label=context.stimulus_label,
                step_id=context.step_id,
                payload=event_payload,
            )
        )


class FrameLockedProtocolEngine(ProtocolEngine):
    """Protocol engine whose visible boundaries commit only on frame acknowledgements."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._pending_events: list[_EventAction] = []
        self._presentation_counter = 0
        self._presentation_revision = 0
        self._presentation_id: str | None = None
        self._pending_control: tuple[str, EventSource] | None = None
        self._event_occurrence_override: tuple[float, datetime] | None = None
        self._control_requested_at: float | None = None
        self._presentation_requested_at: float | None = None

    @property
    def presentation_revision(self) -> int:
        return self._presentation_revision

    @property
    def presentation_id(self) -> str | None:
        return self._presentation_id

    @property
    def awaiting_neutral(self) -> bool:
        return self.state == RunState.AWAITING_NEUTRAL

    def diagnostic_state(self) -> dict[str, object]:
        value = super().diagnostic_state()
        value.update({
            "presentation_id": self._presentation_id,
            "presentation_revision": self._presentation_revision,
            "presentation_counter": self._presentation_counter,
            "pending_control": (
                self._pending_control[0] if self._pending_control is not None else None
            ),
            "pending_control_source": (
                self._pending_control[1].value
                if self._pending_control is not None
                else None
            ),
            "control_requested_at_monotonic_seconds": self._control_requested_at,
            "presentation_requested_at_monotonic_seconds": (
                self._presentation_requested_at
            ),
            "pending_event_count": len(self._pending_events),
            "expected_neutral_previous_presentation_id": (
                self._presentation_id
                if self.state == RunState.AWAITING_NEUTRAL
                else None
            ),
        })
        return value

    def _close_current_trial(self, outcome: str) -> None:
        if self.state == RunState.PAUSED and self._pending_events:
            # Recovery may have prepared a new attempt while the display stays
            # neutral. Until its onset acknowledgement, its queued start events
            # have not happened physically, so superseding it must not invent
            # phase/trial end events for an attempt that never started.
            self._pending_events.clear()
            self._current = None
            self._deadline = None
            self._paused_remaining = 0.0
            self._presentation_id = None
            self._presentation_requested_at = None
            return
        super()._close_current_trial(outcome)

    def _current_attempt_is_unpresented(self) -> bool:
        return self.state == RunState.PAUSED and bool(self._pending_events)

    def start(self) -> None:
        if self.state != RunState.READY:
            raise RuntimeError(f"cannot start protocol from {self.state.value}")
        self._emit(EventType.SESSION_STARTED, _Context(), EventSource.SYSTEM)
        self._prepare_next()

    def tick(self) -> None:
        timeout = self.config.presentation.psychopy.frame_ack_timeout_seconds
        if self.state == RunState.AWAITING_PRESENTATION:
            if (
                self._presentation_requested_at is not None
                and self.clock.monotonic() > self._presentation_requested_at + timeout
            ):
                self.fail("presentation onset acknowledgement timed out")
            return
        if self.state == RunState.AWAITING_NEUTRAL:
            if (
                self._control_requested_at is not None
                and self.clock.monotonic() > self._control_requested_at + timeout
            ):
                self.fail("neutral frame acknowledgement timed out")
            return
        if self.state != RunState.RUNNING or self._deadline is None:
            return
        if self.clock.monotonic() > self._deadline + timeout:
            self.fail("presentation frame acknowledgement timed out")

    def pause(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state != RunState.RUNNING or self._current is None:
            raise RuntimeError(f"cannot pause protocol from {self.state.value}")
        self._pending_control = ("pause", source)
        self._presentation_revision += 1
        self._control_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_NEUTRAL

    def resume(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state != RunState.PAUSED or self._current is None:
            raise RuntimeError(f"cannot resume protocol from {self.state.value}")
        self._pending_control = ("resume", source)
        self._presentation_counter += 1
        self._presentation_revision += 1
        self._presentation_id = self._make_presentation_id(
            self._current, self._presentation_counter
        )
        self._presentation_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_PRESENTATION

    def repeat_current_trial(self, source: EventSource = EventSource.OPERATOR) -> None:
        self._require_active_trial("repeat trial")
        if self.state == RunState.PAUSED:
            # The pause acknowledgement already established a neutral physical
            # frame. Prepare the repeated trial without requesting a redundant
            # neutral flip, and preserve the paused state until Resume.
            super().repeat_current_trial(source)
            return
        self._pending_control = ("repeat_trial", source)
        self._presentation_revision += 1
        self._control_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_NEUTRAL

    def repeat_current_block(self, source: EventSource = EventSource.OPERATOR) -> None:
        self._require_active_trial("repeat block")
        if self.state == RunState.PAUSED:
            super().repeat_current_block(source)
            return
        self._pending_control = ("repeat_block", source)
        self._presentation_revision += 1
        self._control_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_NEUTRAL

    def abort(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state not in {
            RunState.READY,
            RunState.RUNNING,
            RunState.PAUSED,
            RunState.AWAITING_PRESENTATION,
            RunState.AWAITING_NEUTRAL,
        }:
            raise RuntimeError(f"cannot abort protocol from {self.state.value}")
        if self.state == RunState.READY:
            super().abort(source)
            return
        if self.state == RunState.PAUSED:
            # Paused already means neutral has been acknowledged.
            super().abort(source)
            return
        self._pending_control = ("abort", source)
        self._presentation_revision += 1
        self._control_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_NEUTRAL

    def request_failure(self, reason: str) -> None:
        if self.state in TERMINAL_STATES:
            return
        if self.state == RunState.READY:
            self.fail(reason)
            return
        if self.state == RunState.PAUSED:
            self.fail(reason)
            return
        self._pending_control = (f"fail:{reason}", EventSource.SYSTEM)
        self._presentation_revision += 1
        self._control_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_NEUTRAL

    def acknowledge_frame(
        self,
        *,
        previous_presentation_id: str | None,
        presentation_id: str | None,
        revision: int,
        monotonic_seconds: float,
        wall_time_utc: datetime,
        neutral: bool,
    ) -> None:
        occurrence = (monotonic_seconds, wall_time_utc)
        if revision != self._presentation_revision:
            raise ValueError("stale presentation acknowledgement revision")
        if self._pending_control is not None:
            control, source = self._pending_control
            if control == "resume":
                if presentation_id != self._presentation_id or neutral:
                    raise ValueError("resume acknowledgement does not match presentation")
                self._pending_control = None
                self.state = RunState.RUNNING
                self._deadline = monotonic_seconds + self._paused_remaining
                self._paused_remaining = 0.0
                assert self._current is not None
                self._emit(
                    EventType.SESSION_RESUMED,
                    self._current.context,
                    source,
                    occurrence=occurrence,
                )
                # A repeat prepared while paused has already selected its new
                # first phase, but its trial/phase start events must remain
                # pending until that phase is physically presented on Resume.
                self._emit_actions(self._pending_events, occurrence)
                self._pending_events.clear()
                return
            if not neutral or presentation_id is not None:
                raise ValueError("control acknowledgement must confirm a neutral frame")
            if previous_presentation_id != self._presentation_id:
                raise ValueError("neutral acknowledgement does not match current presentation")
            self._pending_control = None
            self._control_requested_at = None
            self._event_occurrence_override = occurrence
            try:
                if control == "pause":
                    assert self._current is not None
                    self._paused_remaining = max(
                        0.0,
                        (self._deadline or monotonic_seconds) - monotonic_seconds,
                    )
                    self._deadline = None
                    self._presentation_id = None
                    self.state = RunState.PAUSED
                    self._emit(
                        EventType.SESSION_PAUSED,
                        self._current.context,
                        source,
                        occurrence=occurrence,
                    )
                elif control == "repeat_trial":
                    self.state = RunState.RUNNING
                    super().repeat_current_trial(source)
                elif control == "repeat_block":
                    self.state = RunState.RUNNING
                    super().repeat_current_block(source)
                elif control == "abort":
                    self.state = RunState.RUNNING
                    super().abort(source)
                elif control.startswith("fail:"):
                    self.state = RunState.RUNNING
                    super().fail(control.removeprefix("fail:"))
            finally:
                self._event_occurrence_override = None
            return

        if self.state == RunState.AWAITING_PRESENTATION:
            if presentation_id != self._presentation_id or neutral:
                raise ValueError("onset acknowledgement does not match current presentation")
            self._emit_actions(self._pending_events, occurrence)
            self._pending_events.clear()
            self._presentation_requested_at = None
            assert self._current is not None
            self._deadline = monotonic_seconds + self._current.duration_seconds
            self.state = RunState.RUNNING
            return

        if self.state != RunState.RUNNING or self._current is None:
            raise RuntimeError("frame acknowledgement is not currently expected")
        if previous_presentation_id != self._presentation_id:
            raise ValueError("transition acknowledgement does not match current presentation")
        next_action, boundary_events, cursor_after = self._peek_next()
        expected_id = (
            self._make_presentation_id(next_action, self._presentation_counter + 1)
            if next_action is not None
            else None
        )
        if presentation_id != expected_id or neutral != (expected_id is None):
            raise ValueError("transition acknowledgement does not match authorized successor")
        self._emit_actions(boundary_events, occurrence)
        if next_action is None:
            self._cursor = cursor_after
            self._current = None
            self._deadline = None
            self._presentation_id = None
            self.state = RunState.COMPLETED
            self._emit(
                EventType.SESSION_COMPLETED,
                _Context(),
                EventSource.SYSTEM,
                occurrence=occurrence,
            )
            return
        self._cursor = cursor_after
        self._current = next_action
        self._presentation_counter += 1
        self._presentation_revision += 1
        self._presentation_id = expected_id
        self._deadline = monotonic_seconds + next_action.duration_seconds

    def confirm_subject_abort(
        self,
        *,
        monotonic_seconds: float,
        wall_time_utc: datetime,
    ) -> None:
        if self.state in TERMINAL_STATES:
            return
        self._pending_control = None
        self._event_occurrence_override = (monotonic_seconds, wall_time_utc)
        try:
            if self.state != RunState.READY:
                self.state = RunState.RUNNING
            super().abort(EventSource.SUBJECT_UI)
        finally:
            self._event_occurrence_override = None

    def view_state(self) -> ViewState:
        if self.state in {RunState.AWAITING_PRESENTATION, RunState.AWAITING_NEUTRAL}:
            if self._current is None:
                return super().view_state()
            return self._view_for(
                self._current,
                self._presentation_id,
                self._presentation_revision,
            )
        value = super().view_state()
        if self._current is not None and self.state in {RunState.RUNNING, RunState.PAUSED}:
            return self._view_for(
                self._current,
                self._presentation_id,
                self._presentation_revision,
            )
        return value

    def successor_view_state(self) -> ViewState | None:
        if self._current is None or self.state not in {
            RunState.RUNNING,
            RunState.AWAITING_PRESENTATION,
        }:
            return None
        action, _, _ = self._peek_next()
        if action is None:
            return None
        return self._view_for(
            action,
            self._make_presentation_id(action, self._presentation_counter + 1),
            self._presentation_revision,
        )

    def _advance_to_timed(self, anchor: float) -> None:
        del anchor
        self._prepare_next()

    def _restart_after_recovery(self, remain_paused: bool) -> None:
        self.state = RunState.RUNNING
        self._prepare_next()
        if (
            remain_paused
            and self._current is not None
            and self.state == RunState.AWAITING_PRESENTATION
        ):
            self._paused_remaining = self._current.duration_seconds
            self._deadline = None
            self._presentation_id = None
            self._presentation_requested_at = None
            self.state = RunState.PAUSED

    def _prepare_next(self) -> None:
        action, events, cursor_after = self._peek_next()
        if action is None:
            self._emit_actions(events, None)
            self.state = RunState.COMPLETED
            self._emit(EventType.SESSION_COMPLETED, _Context(), EventSource.SYSTEM)
            return
        self._current = action
        self._cursor = cursor_after
        self._pending_events = events
        self._presentation_counter += 1
        self._presentation_revision += 1
        self._presentation_id = self._make_presentation_id(
            action, self._presentation_counter
        )
        self._deadline = None
        self._presentation_requested_at = self.clock.monotonic()
        self.state = RunState.AWAITING_PRESENTATION

    def _peek_next(self) -> tuple[_TimedAction | None, list[_EventAction], int]:
        events: list[_EventAction] = []
        cursor = self._cursor
        while cursor < len(self._actions):
            action = self._actions[cursor]
            cursor += 1
            if isinstance(action, _EventAction):
                events.append(action)
                continue
            return action, events, cursor
        return None, events, cursor

    def _emit_actions(
        self,
        actions: list[_EventAction],
        occurrence: tuple[float, datetime] | None,
    ) -> None:
        for action in actions:
            payload = None
            if action.event_type == EventType.TRIAL_STARTED:
                assert action.context.trial_id is not None
                self._attempts[action.context.trial_id] = action.context.attempt or 1
            elif action.event_type == EventType.TRIAL_ENDED:
                payload = {"outcome": "completed"}
            self._emit(
                action.event_type,
                action.context,
                EventSource.ENGINE,
                payload=payload,
                occurrence=occurrence,
            )

    def _view_for(
        self,
        action: _TimedAction,
        presentation_id: str | None,
        revision: int,
    ) -> ViewState:
        context = action.context
        return ViewState(
            run_state=self.state,
            screen=action.screen,
            step_id=context.step_id,
            headline=_headline(action.screen, context.stimulus_label),
            instruction=context.instruction,
            stimulus_id=context.stimulus_id,
            stimulus_label=context.stimulus_label,
            remaining_seconds=(
                self._paused_remaining
                if self.state == RunState.PAUSED
                else self.remaining_seconds
            ),
            duration_seconds=action.duration_seconds,
            block_type=context.block_type,
            block_number=context.block_number,
            block_count=context.block_count,
            trial_number=context.trial_number,
            trial_count=context.trial_count,
            presentation_id=presentation_id,
            revision=revision,
        )

    @staticmethod
    def _make_presentation_id(action: _TimedAction, counter: int) -> str:
        step = action.context.step_id or action.screen
        attempt = action.context.attempt or 0
        return f"presentation-{counter:06d}-{step}-a{attempt}"

    def _emit(
        self,
        event_type: EventType,
        context: _Context,
        source: EventSource,
        payload: dict[str, object] | None = None,
        occurrence: tuple[float, datetime] | None = None,
    ) -> None:
        super()._emit(
            event_type,
            context,
            source,
            payload,
            occurrence or self._event_occurrence_override,
        )
