"""Clock-driven protocol state machine shared by simulations and Qt."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from imagined_speech.config import ExperimentConfig, MarkerConfig, Phase
from imagined_speech.events import (
    EventSink,
    EventSource,
    EventType,
    NullEventSink,
    ProtocolEvent,
)
from imagined_speech.plan import BlockPlan, BreakPlan, RestPlan, SessionPlan


class RunState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


TERMINAL_STATES = {RunState.COMPLETED, RunState.ABORTED, RunState.FAILED}


class ProtocolClock(Protocol):
    def monotonic(self) -> float: ...

    def wall_time_utc(self) -> datetime: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall_time_utc(self) -> datetime:
        return datetime.now(UTC)


class VirtualClock:
    def __init__(self, wall_origin: datetime | None = None) -> None:
        self._monotonic = 0.0
        self._wall_origin = wall_origin or datetime.now(UTC)
        if self._wall_origin.tzinfo is None:
            raise ValueError("virtual clock wall origin must include a timezone")

    def monotonic(self) -> float:
        return self._monotonic

    def wall_time_utc(self) -> datetime:
        return self._wall_origin + timedelta(seconds=self._monotonic)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("virtual time cannot move backwards")
        self._monotonic += seconds


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
    block_type: str | None
    block_number: int | None
    block_count: int | None
    trial_number: int | None
    trial_count: int | None


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
        block_context = _Context(
            step_id=item.block_id,
            block_id=item.block_id,
            block_type=item.block_type,
            block_number=item.block_number,
            block_count=item.block_count,
        )
        actions.append(_EventAction(EventType.BLOCK_STARTED, block_context))
        trial_count = practice_total if item.block_type == "practice" else experiment_total
        for trial in item.trials:
            trial_context = _Context(
                step_id=trial.trial_id,
                block_id=item.block_id,
                block_type=item.block_type,
                block_number=item.block_number,
                block_count=item.block_count,
                trial_id=trial.trial_id,
                trial_number=trial.trial_number,
                trial_count=trial_count,
                attempt=1,
                stimulus_id=trial.stimulus_id,
                stimulus_label=trial.stimulus_label,
            )
            actions.append(_EventAction(EventType.TRIAL_STARTED, trial_context))
            for phase in trial.phases:
                phase_context = _Context(
                    step_id=phase.step_id,
                    block_id=item.block_id,
                    block_type=item.block_type,
                    block_number=item.block_number,
                    block_count=item.block_count,
                    trial_id=trial.trial_id,
                    trial_number=trial.trial_number,
                    trial_count=trial_count,
                    attempt=1,
                    phase=phase.phase,
                    stimulus_id=trial.stimulus_id,
                    stimulus_label=trial.stimulus_label,
                    instruction=phase.instruction,
                )
                actions.append(_EventAction(EventType.PHASE_STARTED, phase_context))
                if phase.phase == Phase.STIMULUS:
                    actions.append(
                        _EventAction(EventType.STIMULUS_PRESENTED, phase_context)
                    )
                actions.append(
                    _TimedAction(
                        screen=phase.phase.value,
                        duration_seconds=phase.duration_seconds,
                        context=phase_context,
                    )
                )
                actions.append(_EventAction(EventType.PHASE_ENDED, phase_context))
            actions.append(_EventAction(EventType.TRIAL_ENDED, trial_context))
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
        self._actions = build_runtime_actions(plan)
        self._cursor = 0
        self._current: _TimedAction | None = None
        self._deadline: float | None = None
        self._paused_remaining = 0.0
        self._sequence = 0
        self._stimulus_indexes = {
            stimulus.id: index for index, stimulus in enumerate(config.stimuli)
        }

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

    def abort(self, source: EventSource = EventSource.OPERATOR) -> None:
        if self.state not in {RunState.RUNNING, RunState.PAUSED}:
            raise RuntimeError(f"cannot abort protocol from {self.state.value}")
        context = self._current.context if self._current else _Context()
        self.state = RunState.ABORTED
        self._emit(EventType.SESSION_ABORTED, context, source)

    def fail(self, reason: str) -> None:
        if self.state in TERMINAL_STATES:
            return
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
                self._emit(action.event_type, action.context, EventSource.ENGINE)
                continue
            self._current = action
            self._deadline = anchor + action.duration_seconds
            return

        self.state = RunState.COMPLETED
        self._emit(EventType.SESSION_COMPLETED, _Context(), EventSource.SYSTEM)

    def _marker_code(self, event_type: EventType, context: _Context) -> int:
        markers: MarkerConfig = self.config.markers
        direct = {
            EventType.SESSION_STARTED: markers.session_start,
            EventType.SESSION_COMPLETED: markers.session_complete,
            EventType.SESSION_ABORTED: markers.session_abort,
            EventType.SESSION_FAILED: markers.session_abort,
            EventType.SESSION_PAUSED: markers.operator_pause,
            EventType.SESSION_RESUMED: markers.operator_resume,
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
    ) -> None:
        self._sequence += 1
        event_payload: dict[str, object] = {}
        if context.scope is not None:
            event_payload["scope"] = context.scope
        if payload:
            event_payload.update(payload)
        self.sink.emit(
            ProtocolEvent(
                sequence_number=self._sequence,
                event_type=event_type,
                marker_code=self._marker_code(event_type, context),
                monotonic_seconds=self.clock.monotonic(),
                wall_time_utc=self.clock.wall_time_utc(),
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
