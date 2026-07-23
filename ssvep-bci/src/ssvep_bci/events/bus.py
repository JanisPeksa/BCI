from __future__ import annotations

from typing import Protocol

from ssvep_bci.events.models import ProtocolEvent


class EventSink(Protocol):
    def emit(self, event: ProtocolEvent) -> None: ...


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event: ProtocolEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[ProtocolEvent] = []

    def emit(self, event: ProtocolEvent) -> None:
        self.events.append(event)
