from __future__ import annotations

import asyncio
import socket

from imagined_speech.ipc.framing import decode_envelope, encode_envelope
from imagined_speech.ipc.messages import (
    ClientRole,
    HelloPayload,
    MessageType,
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
