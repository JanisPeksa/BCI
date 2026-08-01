#!/usr/bin/env python3
"""Free-running photoresistor recorder for the ssvep-bci frequency check.

Reads one newline-terminated ASCII sample (the raw 10-bit ``analogRead``,
0-1023) per loop iteration from an Arduino running
`arduino_light_sensor_sketch` and timestamps every line on the PC with
`time.perf_counter()` -- the exact clock the ssvep-bci app uses for its
`events.jsonl` monotonic timestamps -- plus wall-clock UTC for cross-validation.

The recorder is standalone: start it before `ssvep-bci run`, stop it after the
session ends, then run the analysis against the session folder.

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", required=True,
                        help="serial port, e.g. /dev/ttyACM0 (Uno) or /dev/ttyUSB0 (FTDI)")
    parser.add_argument("--baud", type=int, default=19200,
                        help="must match the Arduino sketch")
    parser.add_argument("--out", default=".",
                        help="directory for light_amp.csv and photosensor_sync.csv")
    parser.add_argument("--warmup", type=int, default=100,
                        help="samples to discard after opening the port")
    parser.add_argument("--batch", type=int, default=7000,
                        help="flush light_amp.csv in batches of this many rows")
    parser.add_argument("--seconds", type=float, default=None,
                        help="stop automatically after N seconds (default: run until Ctrl-C)")
    return parser.parse_args()


def _append_sync(path: Path, kind: str, t_mono: float, t_wall: float) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([kind, f"{t_mono:.12f}", f"{t_wall:.12f}"])


def main() -> int:
    args = parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    light_path = out / "light_amp.csv"
    sync_path = out / "photosensor_sync.csv"

    with light_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(["time_monotonic", "time_wall", "light_amp"])
    with sync_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(["kind", "time_monotonic", "time_wall"])

    arduino = serial.Serial(args.port, args.baud, timeout=0.05)
    arduino.reset_input_buffer()  # drop any stale bytes from the port
    _append_sync(sync_path, "start", time.perf_counter(), time.time())
    print(f"Recording from {args.port} @ {args.baud} baud -> {light_path}")
    print("Note: opening the port resets the Arduino (~2 s); wait for data to flow.")
    print("Press Ctrl-C to stop.")

    buffer: list[tuple[float, float, int]] = []
    pending = b""                 # partial line accumulator (newline framing)
    call = 0
    recorded = 0
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
                try:
                    value = int(raw)
                except ValueError:
                    continue
                t_mono = time.perf_counter()
                t_wall = time.time()
                if call < args.warmup:          # discard serial warm-up samples
                    call += 1
                    continue
                if call == args.warmup:         # one diagnostic sync line
                    call += 1
                    _append_sync(sync_path, "warmup_end", t_mono, t_wall)
                buffer.append((t_mono, t_wall, value))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
