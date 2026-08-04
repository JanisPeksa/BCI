"""Composable protocol-event destinations."""

from __future__ import annotations

from typing import Protocol

from imagined_speech.events.models import ProtocolEvent


class EventSink(Protocol):
    def emit(self, event: ProtocolEvent) -> None: ...


class NullEventSink:
    def emit(self, event: ProtocolEvent) -> None:
        del event


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[ProtocolEvent] = []

    def emit(self, event: ProtocolEvent) -> None:
        self.events.append(event)


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event: ProtocolEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)
