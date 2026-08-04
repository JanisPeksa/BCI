"""UTF-8 JSON Lines framing with a finite message size."""

from __future__ import annotations

import json

from pydantic import ValidationError

from imagined_speech.ipc.messages import Envelope


MAX_LINE_BYTES = 2 * 1024 * 1024


class FramingError(ValueError):
    pass


def encode_envelope(value: Envelope) -> bytes:
    encoded = (value.model_dump_json() + "\n").encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise FramingError("message exceeds the 2 MiB JSONL limit")
    return encoded


def decode_envelope(line: bytes) -> Envelope:
    if len(line) > MAX_LINE_BYTES:
        raise FramingError("message exceeds the 2 MiB JSONL limit")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FramingError("message is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FramingError("message is not valid JSON") from exc
    try:
        return Envelope.model_validate(value)
    except ValidationError as exc:
        raise FramingError(f"message does not match the protocol: {exc}") from exc

