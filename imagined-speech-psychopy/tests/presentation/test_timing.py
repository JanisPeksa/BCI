import pytest

from imagined_speech.presentation.timing import (
    best_calibration,
    calculate_calibration,
    run_preflight,
)


def test_clock_calibration_uses_lowest_rtt_mapping() -> None:
    slow = calculate_calibration(
        sequence=0,
        backend_send_ns=1_000,
        subject_receive_ns=600,
        subject_send_ns=700,
        backend_receive_ns=1_500,
    )
    fast = calculate_calibration(
        sequence=1,
        backend_send_ns=2_000,
        subject_receive_ns=1_600,
        subject_send_ns=1_620,
        backend_receive_ns=2_120,
    )
    assert best_calibration([slow, fast]) == fast
    assert fast.offset_ns == 450
    assert fast.round_trip_ns == 100


class _FakeWindow:
    def __init__(self) -> None:
        self.recordFrameIntervals = False
        self.frameIntervals: list[float] = []
        self.nDroppedFrames = 7
        self.refreshThreshold = 0.0
        self._intervals = iter((1 / 60, 0.02013, 0.02851))

    def flip(self) -> None:
        if self.recordFrameIntervals:
            interval = next(self._intervals)
            self.frameIntervals.append(interval)
            if interval > self.refreshThreshold:
                self.nDroppedFrames += 1

    def getActualFrameRate(self, **_kwargs) -> float:
        return 60.12


def test_preflight_aligns_psychopy_threshold_and_resets_active_counters() -> None:
    window = _FakeWindow()
    result = run_preflight(window, {
        "preflight_warmup_frames": 1,
        "preflight_frame_count": 3,
        "refresh_rate_hz": 60,
        "refresh_rate_tolerance_hz": 0.5,
        "max_frame_interval_factor": 1.5,
        "max_dropped_frame_fraction": 0.34,
    })

    assert window.refreshThreshold == pytest.approx(0.025)
    assert result["metadata"]["dropped_frames"] == 1
    assert result["passed"] is True
    assert window.nDroppedFrames == 0
    assert window.frameIntervals == []
