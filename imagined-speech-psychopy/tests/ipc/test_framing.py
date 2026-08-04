from __future__ import annotations

import json

import pytest

from imagined_speech.ipc.framing import MAX_LINE_BYTES, FramingError, decode_envelope
from imagined_speech.ipc.messages import MessageType, message


def test_jsonl_envelope_round_trip_and_no_authentication_field() -> None:
    encoded = (message(MessageType.HELLO, {"role": "subject"}).model_dump_json() + "\n").encode()
    decoded = decode_envelope(encoded)
    assert decoded.type == MessageType.HELLO
    assert "token" not in decoded.payload
    assert "authentication" not in decoded.payload


@pytest.mark.parametrize(
    "line, match",
    [
        (b"\xff\n", "UTF-8"),
        (b"not-json\n", "valid JSON"),
        (json.dumps({"protocol_version": 1, "type": "unknown"}).encode(), "protocol"),
        (json.dumps({"protocol_version": 9, "type": "hello"}).encode(), "protocol"),
        (b" " * (MAX_LINE_BYTES + 1), "2 MiB"),
    ],
    ids=("utf8", "json", "unknown-type", "version", "oversized"),
)
def test_invalid_frames_are_rejected(line: bytes, match: str) -> None:
    with pytest.raises(FramingError, match=match):
        decode_envelope(line)
