#!/usr/bin/env python3
"""Convert an ssvep-bci session folder to a validated sibling XDF file."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required; run this with the repository virtual environment") from exc


BLOCK_SIZE = 1024
EVENT_BLOCK_SIZE = 256


def vuint(value: int) -> bytes:
    """Encode an XDF variable-length integer."""
    if value < 0:
        raise ValueError("variable-length integer cannot be negative")
    if value <= 0xFF:
        return b"\x01" + struct.pack("<B", value)
    if value <= 0xFFFFFFFF:
        return b"\x04" + struct.pack("<I", value)
    if value <= 0xFFFFFFFFFFFFFFFF:
        return b"\x08" + struct.pack("<Q", value)
    raise ValueError("variable-length integer is too large")


def chunk(tag: int, content: bytes) -> bytes:
    remainder = struct.pack("<H", tag) + content
    return vuint(len(remainder)) + remainder


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def add_text(parent: ET.Element, name: str, value: object) -> None:
    node = ET.SubElement(parent, name)
    node.text = str(value)


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping in {path}")
    return value


def make_stream_header(
    stream_id: int,
    *,
    name: str,
    stream_type: str,
    channel_count: int,
    nominal_rate: float,
    channel_format: str,
    source_id: str,
    description: dict[str, str],
    channels: list[dict[str, str]],
    session_id: str,
    created_at: str,
) -> bytes:
    info = ET.Element("info")
    add_text(info, "name", name)
    add_text(info, "type", stream_type)
    add_text(info, "channel_count", channel_count)
    add_text(info, "nominal_srate", f"{nominal_rate:.12g}")
    add_text(info, "channel_format", channel_format)
    add_text(info, "source_id", source_id)
    add_text(info, "session_id", session_id)
    add_text(info, "created_at", created_at)
    add_text(info, "version", 1)
    desc = ET.SubElement(info, "desc")
    for key, value in description.items():
        add_text(desc, key, value)
    channel_node = ET.SubElement(desc, "channels")
    for specification in channels:
        current = ET.SubElement(channel_node, "channel")
        for key, value in specification.items():
            add_text(current, key, value)
    return struct.pack("<I", stream_id) + xml_bytes(info)


def make_footer(stream_id: int, *, first: float, last: float, count: int, measured_rate: float) -> bytes:
    info = ET.Element("info")
    add_text(info, "first_timestamp", f"{first:.12f}")
    add_text(info, "last_timestamp", f"{last:.12f}")
    add_text(info, "sample_count", count)
    add_text(info, "measured_srate", f"{measured_rate:.12g}")
    return struct.pack("<I", stream_id) + xml_bytes(info)


def protocol_metadata(
    session_config: dict[str, Any],
    config_path: Path,
    pyp_path: Path | None,
    pyp: dict[str, Any] | None,
) -> dict[str, str]:
    protocol = session_config.get("protocol", {})
    if isinstance(session_config.get("dual_stimulus"), dict):
        layout_key = "dual_stimulus"
    elif isinstance(session_config.get("multi_stimulus"), dict):
        layout_key = "multi_stimulus"
    else:
        layout_key = "single_stimulus"
    layout = session_config.get(layout_key) or {}
    stimuli = {
        item["id"]: item
        for item in session_config.get("stimuli", [])
        if isinstance(item, dict) and "id" in item
    }
    target_id = layout.get("target_stimulus_id") or protocol.get("active_stimulus_id")
    distractors = list(layout.get("distractor_stimulus_ids", []))
    positions = list(layout.get("positions", []))
    result = {
        "experiment_id": str(session_config.get("experiment_id", "")),
        "config_file": config_path.name,
        "stimulation_seconds": str(protocol.get("stimulation_seconds", "")),
        "pre_stimulus_seconds": str(protocol.get("pre_stimulus_seconds", "")),
        "inter_trial_seconds": str(protocol.get("inter_trial_seconds", "")),
        "stimulus_layout": layout_key,
        "randomize_conditions": str(layout.get("randomize_conditions", "")),
    }
    if pyp_path is not None:
        result["analysis_pyp"] = pyp_path.name
    if pyp:
        result["analysis_pipeline"] = str(pyp.get("name", ""))
    if target_id:
        result["target_stimulus_id"] = str(target_id)
        result["target_frequency_hz"] = str(stimuli.get(target_id, {}).get("frequency_hz", ""))
    if distractors:
        result["distractor_stimulus_ids"] = json.dumps(distractors, separators=(",", ":"))
        result["distractor_frequencies_hz"] = json.dumps(
            [stimuli.get(item, {}).get("frequency_hz") for item in distractors],
            separators=(",", ":"),
        )
    if stimuli:
        result["configured_stimulus_frequencies_hz"] = json.dumps(
            {item_id: item.get("frequency_hz") for item_id, item in stimuli.items()},
            separators=(",", ":"),
        )
    candidate_frequencies = session_config.get("processing", {}).get("candidate_frequencies_hz")
    if candidate_frequencies:
        result["candidate_frequencies_hz"] = json.dumps(candidate_frequencies, separators=(",", ":"))
    if positions:
        result["square_count"] = str(len(positions))
        result["square_positions"] = json.dumps(positions, separators=(",", ":"))
    elif distractors:
        result["square_count"] = str(1 + len(distractors))
    if layout.get("width_px") is not None or layout.get("height_px") is not None:
        result["square_size_px"] = f"{layout.get('width_px')}x{layout.get('height_px')}"
    return result


def read_source(
    session: Path, metadata: dict[str, Any]
) -> tuple[list[tuple[float, tuple[float, ...], int | None]], list[tuple[float, str]]]:
    channels = list(metadata["channel_names"])
    rows: list[tuple[float, tuple[float, ...], int | None]] = []
    with (session / "eeg_raw.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"sample_index", "source_timestamp", *channels, "embedded_marker"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("eeg_raw.csv is missing required columns")
        for expected_index, row in enumerate(reader):
            if int(row["sample_index"]) != expected_index:
                raise ValueError(f"non-contiguous sample index at row {expected_index}")
            timestamp = float(row["source_timestamp"])
            values = tuple(float(row[channel]) for channel in channels)
            marker_text = row["embedded_marker"].strip()
            marker = None if not marker_text or float(marker_text) == 0.0 else int(round(float(marker_text)))
            rows.append((timestamp, values, marker))
    if any(later[0] < earlier[0] for earlier, later in zip(rows, rows[1:])):
        raise ValueError("source timestamps are not monotonic")

    offset = float(metadata["backend_metadata"]["wall_to_monotonic_offset"])
    events: list[tuple[float, str]] = []
    with (session / "events.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            timestamp = float(event["monotonic_timestamp"]) - offset
            events.append((timestamp, json.dumps(event, separators=(",", ":"), ensure_ascii=False)))
    if any(later[0] < earlier[0] for earlier, later in zip(events, events[1:])):
        raise ValueError("event timestamps are not monotonic")
    expected_count = int(metadata["sample_count"])
    if len(rows) != expected_count:
        raise ValueError(f"metadata sample_count={expected_count}, CSV has {len(rows)} rows")
    return rows, events


def build_file(
    session: Path,
    output: Path,
    metadata: dict[str, Any],
    manifest: dict[str, Any],
    config_path: Path,
    session_config: dict[str, Any],
    pyp_path: Path | None,
    pyp: dict[str, Any] | None,
) -> dict[str, Any]:
    channels = list(metadata["channel_names"])
    rate = float(metadata["configured_sampling_rate_hz"])
    rows, events = read_source(session, metadata)
    protocol = protocol_metadata(session_config, config_path, pyp_path, pyp)
    common = {
        "unit": "uV",
        "backend": str(metadata.get("backend", "")),
        "device_profile_id": str(metadata.get("device_profile_id", "")),
        "timestamp_domain": "unix_epoch_seconds",
        **protocol,
    }
    headers = [
        chunk(1, xml_bytes(ET.fromstring("<info><version>1</version><creator>ssvep-bci session conversion</creator></info>"))),
        chunk(2, make_stream_header(
            1,
            name="Cyton EEG",
            stream_type="EEG",
            channel_count=len(channels),
            nominal_rate=rate,
            channel_format="double64",
            source_id=f"{manifest['session_id']}:eeg",
            description=common,
            channels=[{"label": name, "unit": "uV", "type": "EEG"} for name in channels],
            session_id=manifest["session_id"],
            created_at=manifest["created_at_utc"],
        )),
        chunk(2, make_stream_header(
            2,
            name="BrainFlow Embedded Markers",
            stream_type="Markers",
            channel_count=1,
            nominal_rate=0,
            channel_format="int32",
            source_id=f"{manifest['session_id']}:embedded-markers",
            description={"encoding": "numeric marker code", "source": "eeg_raw.csv embedded_marker", **protocol, "timestamp_domain": "unix_epoch_seconds"},
            channels=[{"label": "marker", "unit": "code", "type": "Markers"}],
            session_id=manifest["session_id"],
            created_at=manifest["created_at_utc"],
        )),
        chunk(2, make_stream_header(
            3,
            name="SSVEP Session Events",
            stream_type="Markers",
            channel_count=1,
            nominal_rate=0,
            channel_format="string",
            source_id=f"{manifest['session_id']}:events",
            description={"encoding": "UTF-8 compact JSON", "source": "events.jsonl", "timestamp_conversion": "event monotonic timestamp minus wall_to_monotonic_offset", **protocol, "timestamp_domain": "unix_epoch_seconds"},
            channels=[{"label": "event", "type": "Markers"}],
            session_id=manifest["session_id"],
            created_at=manifest["created_at_utc"],
        )),
    ]

    sample_chunks: list[tuple[float, bytes]] = []
    for start in range(0, len(rows), BLOCK_SIZE):
        block = rows[start : start + BLOCK_SIZE]
        payload = bytearray(struct.pack("<I", 1))
        payload += vuint(len(block))
        for timestamp, values, _marker in block:
            payload += b"\x08" + struct.pack("<d", timestamp)
            payload += struct.pack("<" + "d" * len(values), *values)
        sample_chunks.append((block[0][0], chunk(3, bytes(payload))))

    embedded = [(timestamp, marker) for timestamp, _values, marker in rows if marker is not None]
    for start in range(0, len(embedded), BLOCK_SIZE):
        block = embedded[start : start + BLOCK_SIZE]
        payload = bytearray(struct.pack("<I", 2))
        payload += vuint(len(block))
        for timestamp, marker in block:
            payload += b"\x08" + struct.pack("<d", timestamp) + struct.pack("<i", marker)
        sample_chunks.append((block[0][0], chunk(3, bytes(payload))))

    for start in range(0, len(events), EVENT_BLOCK_SIZE):
        block = events[start : start + EVENT_BLOCK_SIZE]
        payload = bytearray(struct.pack("<I", 3))
        payload += vuint(len(block))
        for timestamp, value in block:
            raw = value.encode("utf-8")
            payload += b"\x08" + struct.pack("<d", timestamp) + vuint(len(raw)) + raw
        sample_chunks.append((block[0][0], chunk(3, bytes(payload))))
    sample_chunks.sort(key=lambda item: item[0])

    first, last = rows[0][0], rows[-1][0]
    measured_rate = (len(rows) - 1) / (last - first)
    marker_times = [item[0] for item in embedded]
    event_times = [item[0] for item in events]
    footers = [
        chunk(6, make_footer(1, first=first, last=last, count=len(rows), measured_rate=measured_rate)),
        chunk(6, make_footer(2, first=marker_times[0] if marker_times else first, last=marker_times[-1] if marker_times else last, count=len(embedded), measured_rate=0)),
        chunk(6, make_footer(3, first=event_times[0] if event_times else first, last=event_times[-1] if event_times else last, count=len(events), measured_rate=0)),
    ]

    with output.open("xb") as handle:
        handle.write(b"XDF:")
        for item in headers:
            handle.write(item)
        for _timestamp, item in sample_chunks:
            handle.write(item)
        for item in footers:
            handle.write(item)

    return {
        "output": str(output.resolve()),
        "bytes": output.stat().st_size,
        "eeg_samples": len(rows),
        "eeg_channels": channels,
        "embedded_markers": len(embedded),
        "events": len(events),
        "measured_rate_hz": measured_rate,
        "protocol": protocol,
    }


def read_vuint(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise ValueError("truncated variable-length integer")
    width = data[position]
    position += 1
    if width == 1:
        size, fmt = 1, "<B"
    elif width == 4:
        size, fmt = 4, "<I"
    elif width == 8:
        size, fmt = 8, "<Q"
    else:
        raise ValueError(f"invalid XDF variable integer width {width}")
    if position + size > len(data):
        raise ValueError("truncated variable-length integer payload")
    return struct.unpack_from(fmt, data, position)[0], position + size


def validate_xdf(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:4] != b"XDF:":
        raise ValueError("XDF magic header is missing")
    position = 4
    headers: dict[int, dict[str, Any]] = {}
    counts: dict[int, int] = {}
    footers: set[int] = set()
    while position < len(data):
        remainder_length, position = read_vuint(data, position)
        end = position + remainder_length
        if end > len(data) or remainder_length < 2:
            raise ValueError("invalid XDF chunk length")
        tag = struct.unpack_from("<H", data, position)[0]
        position += 2
        if tag == 1:
            ET.fromstring(data[position:end])
        elif tag == 2:
            stream_id = struct.unpack_from("<I", data, position)[0]
            info = ET.fromstring(data[position + 4 : end])
            desc = info.find("desc")
            headers[stream_id] = {
                "count": int(info.findtext("channel_count")),
                "format": info.findtext("channel_format"),
                "name": info.findtext("name"),
                "desc": {child.tag: child.text for child in desc} if desc is not None else {},
            }
        elif tag == 3:
            stream_id = struct.unpack_from("<I", data, position)[0]
            cursor = position + 4
            count, cursor = read_vuint(data, cursor)
            spec = headers[stream_id]
            for _ in range(count):
                timestamp_width = data[cursor]
                cursor += 1
                if timestamp_width == 8:
                    cursor += 8
                elif timestamp_width != 0:
                    raise ValueError("invalid XDF sample timestamp width")
                if spec["format"] == "double64":
                    cursor += 8 * spec["count"]
                elif spec["format"] == "int32":
                    cursor += 4 * spec["count"]
                elif spec["format"] == "string":
                    length, cursor = read_vuint(data, cursor)
                    cursor += length
                else:
                    raise ValueError(f"unsupported stream format {spec['format']}")
                if cursor > end:
                    raise ValueError("sample exceeds XDF chunk boundary")
            if cursor != end:
                raise ValueError("XDF sample chunk has trailing or missing bytes")
            counts[stream_id] = counts.get(stream_id, 0) + count
        elif tag == 6:
            stream_id = struct.unpack_from("<I", data, position)[0]
            ET.fromstring(data[position + 4 : end])
            footers.add(stream_id)
        position = end
    if position != len(data):
        raise ValueError("XDF parser did not consume the file")
    expected_counts = {1: expected["eeg_samples"], 2: expected["embedded_markers"], 3: expected["events"]}
    if counts != expected_counts:
        raise ValueError(f"XDF sample counts {counts} do not match {expected_counts}")
    if footers != {1, 2, 3}:
        raise ValueError(f"missing stream footers: {footers}")
    expected_names = {1: "Cyton EEG", 2: "BrainFlow Embedded Markers", 3: "SSVEP Session Events"}
    if {key: value["name"] for key, value in headers.items()} != expected_names:
        raise ValueError("XDF stream names do not match the stream contract")
    return {"bytes": len(data), "samples": counts, "headers": headers, "footers": sorted(footers)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="completed ssvep-bci session directory")
    parser.add_argument("--config", type=Path, help="reference YAML configuration used by the session")
    parser.add_argument("--pyp", type=Path, help="optional analysis pipeline .pyp file")
    parser.add_argument("--output", type=Path, help="output XDF path; defaults to a sibling of the session")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output explicitly")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not session.is_dir():
        raise SystemExit(f"session directory not found: {session}")
    output = (args.output or session.with_suffix(".xdf")).expanduser().resolve()
    if output.exists() and not args.overwrite:
        raise SystemExit(f"output exists; pass --overwrite only with explicit authorization: {output}")
    if output.parent == session:
        raise SystemExit("output must be outside the source session directory")
    config_path = (args.config or (session / "experiment-config.yaml")).expanduser().resolve()
    if not config_path.is_file():
        raise SystemExit(f"configuration file not found: {config_path}")
    pyp_path = args.pyp.expanduser().resolve() if args.pyp else None
    if pyp_path is not None and not pyp_path.is_file():
        raise SystemExit(f"PYP file not found: {pyp_path}")

    metadata = json.loads((session / "acquisition-metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    session_config = load_yaml(session / "experiment-config.yaml")
    reference_config = load_yaml(config_path)
    if session_config.get("experiment_id") != reference_config.get("experiment_id"):
        raise SystemExit("saved session configuration does not match the reference configuration")
    pyp = load_yaml(pyp_path) if pyp_path else None

    if args.overwrite and output.exists():
        output.unlink()
    result = build_file(session, output, metadata, manifest, config_path, session_config, pyp_path, pyp)
    result["xdf_validation"] = validate_xdf(output, result)
    result["sha256"] = sha256(output)
    result["source_folder_contains_xdf"] = any(path.suffix.lower() == ".xdf" for path in session.iterdir())
    if result["source_folder_contains_xdf"]:
        raise SystemExit("source session folder contains an XDF; conversion safety check failed")

    try:
        sys.path.insert(0, str(session.parent.parent / "src"))
        from ssvep_bci.recording import validate_session  # type: ignore

        result["session_validation"] = validate_session(session)
    except (ImportError, ModuleNotFoundError):
        result["session_validation"] = "unavailable"
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
