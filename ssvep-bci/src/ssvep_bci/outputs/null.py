from __future__ import annotations

from ssvep_bci.outputs.base import SelectionDecision


class NullOutputSink:
    """Explicit first-iteration sink: decisions have no external side effects."""

    def emit(self, decision: SelectionDecision) -> None:
        return None

    def close(self) -> None:
        return None
