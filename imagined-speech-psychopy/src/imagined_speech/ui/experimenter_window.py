"""Operator-window state policy independent of the backend runtime.

The concrete Qt widgets live in :mod:`imagined_speech.ui.application`; this
module deliberately contains no local ``SessionRuntime`` or subject-window
ownership. That separation prevents the operator process from regaining access
to acquisition buffers or session writers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorControlState:
    create_session: bool
    start_protocol: bool
    pause: bool
    resume: bool
    repeat: bool
    abort: bool


def controls_for(runtime_state: str | None, engine_state: str | None) -> OperatorControlState:
    terminal = runtime_state in {"finalized", "failed"}
    no_session = runtime_state is None or terminal
    return OperatorControlState(
        create_session=no_session,
        start_protocol=runtime_state == "ready" and engine_state == "ready",
        pause=engine_state == "running",
        resume=engine_state == "paused",
        repeat=engine_state in {
            "running",
            "paused",
            "awaiting_presentation",
            "awaiting_neutral",
        },
        abort=runtime_state is not None and not terminal,
    )
