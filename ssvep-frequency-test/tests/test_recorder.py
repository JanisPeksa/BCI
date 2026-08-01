from __future__ import annotations

from record_photosensor import parse_serial_sample


def test_parse_device_timed_sample() -> None:
    assert parse_serial_sample(b"123,456789,812\r") == (123, 456789, 812)


def test_parse_legacy_sample() -> None:
    assert parse_serial_sample(b"812") == (None, None, 812)


def test_ignore_header_and_malformed_lines() -> None:
    assert parse_serial_sample(b"sample_index,device_time_us,light_amp") is None
    assert parse_serial_sample(b"not-a-sample") is None
