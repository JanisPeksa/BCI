from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from imagined_speech.ipc.messages import (
    FrameAcknowledgementPayload,
    MessageType,
    message,
)
from imagined_speech.ipc.server import BackendService
from imagined_speech.runtime.coordinator import SessionRuntimeState


def test_terminal_runtime_sends_final_notification_without_more_snapshots() -> None:
    async def scenario() -> tuple[list[MessageType], int]:
        service = BackendService()
        service.runtime = SimpleNamespace(
            state=SessionRuntimeState.FINALIZED,
            tick=lambda: None,
            needs_final_clock_calibration=False,
            presentation_state=lambda: None,
            validation_report=None,
            session_path="sessions/test",
            writer=SimpleNamespace(
                session_id="session-test",
                participant_id="P001",
                session_label="RUN001",
            ),
            resolved=SimpleNamespace(
                config=SimpleNamespace(experiment_id="smoke"),
                device=SimpleNamespace(profile_id="synthetic"),
            ),
            error=None,
            timing_warnings=[],
            engine=SimpleNamespace(
                failure_reason=None,
                diagnostic_state=lambda: {"engine_state": "completed"},
            ),
        )
        published: list[MessageType] = []
        snapshots = 0

        async def broadcast(envelope) -> None:  # type: ignore[no-untyped-def]
            published.append(envelope.type)
            service._shutdown.set()

        async def snapshot(_runtime) -> None:  # type: ignore[no-untyped-def]
            nonlocal snapshots
            snapshots += 1

        async def no_op() -> None:
            return None

        service._broadcast = broadcast  # type: ignore[method-assign]
        service._send_operator_snapshot = snapshot  # type: ignore[method-assign]
        service._send_service_state = no_op  # type: ignore[method-assign]

        await asyncio.wait_for(service._tick_loop(), timeout=1)
        return published, snapshots

    published, snapshots = asyncio.run(scenario())

    assert published == [MessageType.SESSION_FINALIZED]
    assert snapshots == 0


def test_frame_ack_publishes_operator_snapshot_before_next_presentation() -> None:
    async def scenario() -> list[str]:
        service = BackendService()
        service.runtime = SimpleNamespace(
            writer=SimpleNamespace(session_id="session-test"),
            acknowledge_frame=lambda *_args, **_kwargs: None,
        )
        publications: list[str] = []

        async def snapshot(_runtime) -> None:  # type: ignore[no-untyped-def]
            publications.append("operator_snapshot")

        async def presentation() -> None:
            publications.append("presentation_state")

        service._send_operator_snapshot = snapshot  # type: ignore[method-assign]
        service._send_presentation_state = presentation  # type: ignore[method-assign]
        await service._frame_ack(message(
            MessageType.FRAME_ACK,
            FrameAcknowledgementPayload(
                presentation_id="presentation-1",
                subject_monotonic_ns=1,
                wall_time_utc=datetime(2026, 1, 1, tzinfo=UTC),
                frame_index=1,
            ),
            session_id="session-test",
            revision=1,
        ))
        return publications

    assert asyncio.run(scenario()) == [
        "operator_snapshot",
        "presentation_state",
    ]
