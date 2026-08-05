"""Shared monitoring-widget projection contract."""

from typing import Protocol

from imagined_speech.ipc.messages import OperatorStatePayload


class MonitoringWidget(Protocol):
    def render(self, state: OperatorStatePayload) -> None: ...

    def clear(self) -> None: ...
