import asyncio
from types import SimpleNamespace

from imagined_speech.ipc.server import BackendService


def test_identical_presentation_revision_is_dispatched_once() -> None:
    async def scenario() -> None:
        timing_records: list[dict] = []
        sent: list[object] = []
        state = {
            "revision": 4,
            "interrupt": True,
            "current": {"presentation_id": "presentation-4"},
            "successor": None,
        }
        runtime = SimpleNamespace(
            presentation_state=lambda: state,
            engine=SimpleNamespace(
                state=SimpleNamespace(value="awaiting_neutral"),
                diagnostic_state=lambda: {"pending_control": "pause"},
            ),
            writer=SimpleNamespace(
                session_id="session-1",
                record_presentation_timing=timing_records.append,
            ),
        )
        service = BackendService()
        service.runtime = runtime

        async def capture(_role, value, **_kwargs) -> None:
            sent.append(value)

        service._send_role = capture  # type: ignore[method-assign]
        await service._send_presentation_state()
        await service._send_presentation_state()

        assert len(sent) == 1
        assert len(timing_records) == 1

    asyncio.run(scenario())
