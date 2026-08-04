import asyncio

from imagined_speech.ipc.clients import JsonlClient
from imagined_speech.ipc.messages import ClientRole
from imagined_speech.ipc.server import _Connection, _close_stream_writer


class _ResetStreamWriter:
    def __init__(self, error: OSError) -> None:
        self.error = error
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1

    async def wait_closed(self) -> None:
        raise self.error


def test_stream_close_ignores_windows_peer_reset() -> None:
    async def scenario() -> None:
        writer = _ResetStreamWriter(
            ConnectionResetError(10054, "remote host forcibly closed the connection")
        )
        await _close_stream_writer(writer)  # type: ignore[arg-type]
        assert writer.close_count == 1

    asyncio.run(scenario())


def test_connection_close_is_idempotent_after_network_name_loss() -> None:
    async def scenario() -> None:
        writer = _ResetStreamWriter(OSError(64, "network name is no longer available"))
        connection = _Connection(ClientRole.SUBJECT, writer)  # type: ignore[arg-type]

        await connection.close()
        await connection.close()

        assert writer.close_count == 1
        assert connection.task.done()

    asyncio.run(scenario())


class _ResetSocket:
    def shutdown(self, _how: int) -> None:
        raise OSError(10054, "already reset")

    def close(self) -> None:
        raise OSError(64, "network name lost")


def test_threaded_client_close_ignores_already_reset_socket() -> None:
    client = JsonlClient("127.0.0.1", 1)
    client._socket = _ResetSocket()  # type: ignore[assignment]

    client.close()

    assert client._closed.is_set()
