#!/usr/bin/env python3
"""Independent free-running photoresistor recorder for SSVEP frequency checks.

Reads newline-terminated ``sample_index,device_time_us,light_amp`` records from
an Arduino running `arduino_light_sensor_sketch`. Device time provides the
frequency clock; host monotonic and wall-clock receipt times align the device
stream to sessions produced by ssvep-bci and psychopy-ssvep. Legacy sketches
that emit only one light value per line remain supported.

The recorder is standalone: start it before either experiment app, stop it
after the session ends, then run the analysis against the session folder.

Usage:
    python record_photosensor.py --port /dev/ttyACM0 --out ./measurement_01
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError as exc:  # pragma: no cover
    sys.exit("error: pyserial is required (python -m pip install pyserial)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", required=True,
                        help="serial port, e.g. /dev/ttyACM0 (Uno) or /dev/ttyUSB0 (FTDI)")
    parser.add_argument("--baud", type=int, default=115200,
                        help="must match the Arduino sketch")
    parser.add_argument("--out", default=".",
                        help="directory for light_amp.csv and photosensor_sync.csv")
    parser.add_argument("--warmup", type=int, default=100,
                        help="samples to discard after opening the port")
    parser.add_argument("--batch", type=int, default=7000,
                        help="flush light_amp.csv in batches of this many rows")
    parser.add_argument("--seconds", type=float, default=None,
                        help="stop automatically after N seconds (default: run until Ctrl-C)")
    return parser.parse_args(argv)


def _append_sync(
    path: Path,
    kind: str,
    t_mono: float,
    t_wall: float,
    sample_index: int | None = None,
    device_time_us: int | None = None,
) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            kind,
            f"{t_mono:.12f}",
            f"{t_wall:.12f}",
            "" if sample_index is None else sample_index,
            "" if device_time_us is None else device_time_us,
        ])


def parse_serial_sample(raw: bytes) -> tuple[int | None, int | None, int] | None:
    """Parse the device-timed protocol or the legacy one-value protocol."""
    try:
        fields = raw.decode("ascii").strip().split(",")
    except UnicodeDecodeError:
        return None
    try:
        if len(fields) == 3:
            sample_index, device_time_us, light_amp = (int(value) for value in fields)
            if sample_index < 0 or device_time_us < 0:
                return None
            return sample_index, device_time_us, light_amp
        if len(fields) == 1:
            return None, None, int(fields[0])
    except ValueError:
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    light_path = out / "light_amp.csv"
    sync_path = out / "photosensor_sync.csv"

    with light_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            "time_monotonic",
            "time_wall",
            "light_amp",
            "sample_index",
            "device_time_us",
        ])
    with sync_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            "kind",
            "time_monotonic",
            "time_wall",
            "sample_index",
            "device_time_us",
        ])

    arduino = serial.Serial(args.port, args.baud, timeout=0.05)
    arduino.reset_input_buffer()  # drop any stale bytes from the port
    _append_sync(sync_path, "start", time.perf_counter(), time.time())
    print(f"Recording from {args.port} @ {args.baud} baud -> {light_path}")
    print("Note: opening the port resets the Arduino (~2 s); wait for data to flow.")
    print("Press Ctrl-C to stop.")

    buffer: list[tuple[float, float, int, int | str, int | str]] = []
    pending = b""                 # partial line accumulator (newline framing)
    call = 0
    recorded = 0
    invalid = 0
    device_timed = 0
    missing_samples = 0
    previous_index: int | None = None
    start = time.perf_counter()
    try:
        while True:
            if args.seconds is not None and time.perf_counter() - start > args.seconds:
                break
            chunk = arduino.read(arduino.in_waiting or 1)
            if not chunk:
                continue
            pending += chunk
            while b"\n" in pending:
                line, _, pending = pending.partition(b"\n")
                raw = line.strip()
                if not raw:
                    continue
                parsed = parse_serial_sample(raw)
                if parsed is None:
                    invalid += 1
                    continue
                sample_index, device_time_us, value = parsed
                t_mono = time.perf_counter()
                t_wall = time.time()
                if call < args.warmup:          # discard serial warm-up samples
                    call += 1
                    continue
                if call == args.warmup:         # one diagnostic sync line
                    call += 1
                    _append_sync(
                        sync_path,
                        "warmup_end",
                        t_mono,
                        t_wall,
                        sample_index,
                        device_time_us,
                    )
                if sample_index is not None:
                    device_timed += 1
                    if previous_index is not None and sample_index > previous_index + 1:
                        missing_samples += sample_index - previous_index - 1
                    previous_index = sample_index
                buffer.append((
                    t_mono,
                    t_wall,
                    value,
                    "" if sample_index is None else sample_index,
                    "" if device_time_us is None else device_time_us,
                ))
                recorded += 1
                call += 1
                if len(buffer) >= args.batch:
                    with light_path.open("a", newline="", encoding="utf-8") as handle:
                        csv.writer(handle).writerows(buffer)
                    buffer.clear()
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        if buffer:
            with light_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(buffer)
        _append_sync(sync_path, "end", time.perf_counter(), time.time())
        arduino.close()
    recorded = max(0, recorded)
    elapsed = max(time.perf_counter() - start, 1e-9)
    if recorded == 0:
        print("WARNING: recorded 0 samples. The Arduino resets ~2 s after the "
              "port opens, and the first 100 samples are warm-up. Make sure you "
              "let it run for a few seconds (or use --seconds N), that the "
              "10-bit ASCII sketch is uploaded, and that --port is right "
              f"(found {args.port}).")
    else:
        print(f"wrote {recorded} samples to {light_path} "
              f"({recorded / elapsed:.0f} Hz including reset+warm-up)")
        protocol = "device-timed" if device_timed == recorded else "legacy host-timed"
        print(f"protocol: {protocol}; ignored non-sample lines: {invalid}; "
              f"missing device samples: {missing_samples}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
