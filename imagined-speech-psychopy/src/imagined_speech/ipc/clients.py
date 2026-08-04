"""Nonblocking threaded JSONL client used by the PsychoPy process."""

from __future__ import annotations

import queue
import socket
import threading
from collections.abc import Iterator

from imagined_speech.ipc.framing import MAX_LINE_BYTES, decode_envelope, encode_envelope
from imagined_speech.ipc.messages import Envelope


class JsonlClient:
    def __init__(self, host: str, port: int, *, queue_size: int = 256) -> None:
        self.host = host
        self.port = port
        self.incoming: queue.Queue[Envelope] = queue.Queue(maxsize=queue_size)
        self.outgoing: queue.Queue[Envelope | None] = queue.Queue(maxsize=queue_size)
        self._socket: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._closed = threading.Event()
        self.error: Exception | None = None

    def connect(self, timeout: float = 5.0) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(None)
        self._socket = sock
        self._threads = [
            threading.Thread(target=self._read_loop, name="ipc-reader", daemon=True),
            threading.Thread(target=self._write_loop, name="ipc-writer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def send(self, value: Envelope, timeout: float = 0.0) -> None:
        if self._closed.is_set():
            raise RuntimeError("IPC client is closed")
        try:
            if timeout > 0:
                self.outgoing.put(value, timeout=timeout)
            else:
                self.outgoing.put_nowait(value)
        except queue.Full as exc:
            raise RuntimeError("IPC writer queue is full") from exc

    def receive_nowait(self) -> Envelope | None:
        try:
            return self.incoming.get_nowait()
        except queue.Empty:
            return None

    def messages(self) -> Iterator[Envelope]:
        while not self._closed.is_set() or not self.incoming.empty():
            try:
                yield self.incoming.get(timeout=0.1)
            except queue.Empty:
                continue

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.outgoing.put_nowait(None)
        except queue.Full:
            pass
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=1)

    def _read_loop(self) -> None:
        assert self._socket is not None
        try:
            with self._socket.makefile("rb") as handle:
                while not self._closed.is_set():
                    line = handle.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    self.incoming.put(decode_envelope(line), timeout=1)
        except Exception as exc:
            if not self._closed.is_set():
                self.error = exc
        finally:
            self._closed.set()

    def _write_loop(self) -> None:
        assert self._socket is not None
        try:
            while not self._closed.is_set():
                value = self.outgoing.get()
                if value is None:
                    return
                self._socket.sendall(encode_envelope(value))
        except Exception as exc:
            if not self._closed.is_set():
                self.error = exc
        finally:
            self._closed.set()
