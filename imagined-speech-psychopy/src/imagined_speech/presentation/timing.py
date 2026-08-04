"""Display preflight and cross-process monotonic-clock calibration helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClockCalibration:
    offset_ns: int
    round_trip_ns: int
    sequence: int


def calculate_calibration(
    *,
    sequence: int,
    backend_send_ns: int,
    subject_receive_ns: int,
    subject_send_ns: int,
    backend_receive_ns: int,
) -> ClockCalibration:
    round_trip = max(
        0,
        (backend_receive_ns - backend_send_ns)
        - (subject_send_ns - subject_receive_ns),
    )
    backend_midpoint = (backend_send_ns + backend_receive_ns) // 2
    subject_midpoint = (subject_receive_ns + subject_send_ns) // 2
    return ClockCalibration(
        offset_ns=backend_midpoint - subject_midpoint,
        round_trip_ns=round_trip,
        sequence=sequence,
    )


def best_calibration(values: list[ClockCalibration]) -> ClockCalibration | None:
    return min(values, key=lambda value: value.round_trip_ns) if values else None


def run_preflight(window, config: dict) -> dict:
    expected = 1.0 / float(config["refresh_rate_hz"])
    threshold = expected * float(config["max_frame_interval_factor"])
    # Keep PsychoPy's dropped-frame counter/logging on the same definition used
    # by the backend. Its default threshold is intentionally more sensitive and
    # otherwise reports frames which this experiment still considers acceptable.
    window.refreshThreshold = threshold
    for _ in range(int(config["preflight_warmup_frames"])):
        window.flip()
    measured = window.getActualFrameRate(
        nIdentical=10,
        nMaxFrames=int(config["preflight_frame_count"]),
        nWarmUpFrames=0,
        threshold=1,
    )
    measured = float(measured or 0.0)
    window.recordFrameIntervals = True
    window.frameIntervals = []
    for _ in range(int(config["preflight_frame_count"])):
        window.flip()
    intervals = tuple(float(value) for value in window.frameIntervals)
    window.recordFrameIntervals = False
    dropped = sum(value > threshold for value in intervals)
    fraction = dropped / len(intervals) if intervals else 1.0
    refresh_ok = (
        measured > 0
        and abs(measured - float(config["refresh_rate_hz"]))
        <= float(config["refresh_rate_tolerance_hz"])
    )
    drops_ok = fraction <= float(config["max_dropped_frame_fraction"])
    # Preflight has its own diagnostics. Active-session counters and intervals
    # must start clean so their fractions describe only subject-visible phases.
    window.nDroppedFrames = 0
    window.frameIntervals = []
    return {
        "passed": refresh_ok and drops_ok,
        "measured_refresh_rate_hz": measured,
        "dropped_frame_fraction": fraction,
        "frame_interval_count": len(intervals),
        "frame_intervals_seconds": intervals,
        "metadata": {
            "expected_refresh_rate_hz": config["refresh_rate_hz"],
            "refresh_ok": refresh_ok,
            "dropped_frames": dropped,
            "drops_ok": drops_ok,
        },
    }
