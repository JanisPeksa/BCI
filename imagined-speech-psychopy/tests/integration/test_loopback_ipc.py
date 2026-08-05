from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from imagined_speech.cli import default_config_path
from imagined_speech.ipc.framing import decode_envelope, encode_envelope
from imagined_speech.ipc.messages import (
    ClientRole,
    CreateSessionPayload,
    HelloPayload,
    MessageType,
    ServiceStatePayload,
    SubjectDisplayTargetPayload,
    message,
)
from imagined_speech.ipc.server import BackendService


async def _connect(port: int):
    for _ in range(100):
        try:
            return await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.01)
    raise AssertionError("backend did not start")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def test_real_loopback_handshake_has_no_auth_and_rejects_role_conflict() -> None:
    async def scenario() -> None:
        port = _free_port()
        service = BackendService()
        server_task = asyncio.create_task(service.serve("127.0.0.1", port))

        operator_reader, operator_writer = await _connect(port)
        operator_writer.write(encode_envelope(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.OPERATOR,
                software_version="test",
                process_id=1,
            ),
        )))
        await operator_writer.drain()
        accepted = decode_envelope(await operator_reader.readline())
        assert accepted.type == MessageType.HELLO_ACCEPTED
        initial_service = decode_envelope(await operator_reader.readline())
        assert initial_service.type == MessageType.SERVICE_STATE
        assert not ServiceStatePayload.model_validate(
            initial_service.payload
        ).subject_connected
        assert ServiceStatePayload.model_validate(
            initial_service.payload
        ).can_create_session

        subject_reader, subject_writer = await _connect(port)
        subject_writer.write(encode_envelope(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.SUBJECT,
                software_version="test",
                process_id=2,
            ),
        )))
        await subject_writer.drain()
        assert decode_envelope(await subject_reader.readline()).type == MessageType.HELLO_ACCEPTED
        ready_service = decode_envelope(await operator_reader.readline())
        assert ready_service.type == MessageType.SERVICE_STATE
        ready = ServiceStatePayload.model_validate(ready_service.payload)
        assert ready.subject_connected
        assert not ready.can_create_session

        duplicate_reader, duplicate_writer = await _connect(port)
        duplicate_writer.write(encode_envelope(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.SUBJECT,
                software_version="test",
                process_id=3,
            ),
        )))
        await duplicate_writer.drain()
        conflict = decode_envelope(await duplicate_reader.readline())
        assert conflict.type == MessageType.ERROR
        assert "already connected" in conflict.payload["error"]
        duplicate_writer.close()
        await duplicate_writer.wait_closed()

        operator_writer.write(encode_envelope(message(MessageType.SHUTDOWN)))
        await operator_writer.drain()
        await asyncio.wait_for(server_task, timeout=3)
        operator_writer.close()
        subject_writer.close()

    asyncio.run(scenario())


def test_session_creation_does_not_require_or_initialize_subject(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = BackendService()

        await service._create_session(CreateSessionPayload(
            config_path=str(default_config_path()),
            participant_id="LAZY001",
            output_root=str(tmp_path),
            screen_index=1,
            subject_display=SubjectDisplayTargetPayload(
                device_name=r"\\.\DISPLAY1",
                psychopy_index=0,
                qt_index=1,
                qt_name=r"\\.\DISPLAY1",
                geometry=(-1920, 1045, 1920, 1080),
            ),
        ))

        assert service.runtime is not None
        assert service.runtime.state.value == "created"
        assert service.runtime.resolved.config.presentation.psychopy.screen_index == 0
        assert service._subject_init is not None
        assert (
            service._subject_init.payload["display_target"]["device_name"]
            == r"\\.\DISPLAY1"
        )
        assert service._clock_phase is None
        assert ClientRole.SUBJECT not in service.connections
        service.runtime.close()

    asyncio.run(scenario())


def test_subject_connection_after_session_creation_starts_initialization(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        port = _free_port()
        service = BackendService()
        server_task = asyncio.create_task(service.serve("127.0.0.1", port))

        operator_reader, operator_writer = await _connect(port)
        operator_writer.write(encode_envelope(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.OPERATOR,
                software_version="test",
                process_id=10,
            ),
        )))
        await operator_writer.drain()
        assert decode_envelope(
            await operator_reader.readline()
        ).type == MessageType.HELLO_ACCEPTED
        assert decode_envelope(
            await operator_reader.readline()
        ).type == MessageType.SERVICE_STATE

        operator_writer.write(encode_envelope(message(
            MessageType.CREATE_SESSION,
            CreateSessionPayload(
                config_path=str(default_config_path()),
                participant_id="LAZY002",
                output_root=str(tmp_path),
            ),
        )))
        await operator_writer.drain()

        subject_reader, subject_writer = await _connect(port)
        subject_writer.write(encode_envelope(message(
            MessageType.HELLO,
            HelloPayload(
                role=ClientRole.SUBJECT,
                software_version="test",
                process_id=11,
            ),
        )))
        await subject_writer.drain()
        assert decode_envelope(
            await subject_reader.readline()
        ).type == MessageType.HELLO_ACCEPTED
        assert decode_envelope(
            await subject_reader.readline()
        ).type == MessageType.CLOCK_PING

        service._shutdown.set()
        await asyncio.wait_for(server_task, timeout=3)
        operator_writer.close()
        subject_writer.close()

    asyncio.run(scenario())
