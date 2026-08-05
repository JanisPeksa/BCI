import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from imagined_speech.ipc.messages import OperatorStatePayload
from imagined_speech.ui.widgets.eeg_trace import EEGTraceWidget


_APPLICATION = QApplication.instance() or QApplication([])


def _app() -> QApplication:
    return _APPLICATION


def _state(
    sample_count: int,
    recent_samples: tuple[tuple[float, ...], ...],
    *,
    session_id: str = "session-a",
    indexes: tuple[int, ...] = (0,),
    labels: tuple[str, ...] = ("F3",),
    sampling_rate_hz: float = 250,
) -> OperatorStatePayload:
    channel_count = max(indexes, default=0) + 1
    channel_names = tuple(f"channel-{index}" for index in range(channel_count))
    return OperatorStatePayload.model_validate({
        "runtime_state": "running",
        "engine_state": "running",
        "protocol_started": True,
        "recording": True,
        "session_path": "sessions/test",
        "session_id": session_id,
        "participant_id": "P001",
        "session_label": "RUN001",
        "experiment_id": "sweep-test",
        "device_profile_id": "synthetic",
        "view_state": {
            "run_state": "running",
            "screen": "thinking",
            "step_id": "step-1",
            "headline": "Think",
            "instruction": "Imagine the word",
            "stimulus_id": "left",
            "stimulus_label": "Left",
            "remaining_seconds": 1.0,
            "duration_seconds": 2.0,
            "stage_type": "experiment",
            "block_number": 1,
            "block_count": 1,
            "trial_number": 1,
            "trial_count": 1,
            "revision": 1,
        },
        "acquisition": {
            "running": True,
            "sample_count": sample_count,
            "dropped_batches": 0,
            "dropped_samples": 0,
            "timestamp_discontinuities": 0,
            "read_errors": 0,
            "write_errors": 0,
            "last_health_kind": "receiving",
            "last_health_severity": "info",
            "channel_names": channel_names,
            "recent_samples": recent_samples,
            "sampling_rate_hz": sampling_rate_hz,
            "eeg_channel_indexes": indexes,
            "eeg_channel_labels": labels,
            "raw_file_size_bytes": 0,
            "free_storage_bytes": 1,
        },
    })


def _samples(start: int, stop: int) -> tuple[tuple[float, ...], ...]:
    return tuple((float(index),) for index in range(start, stop))


def test_initial_snapshot_bootstraps_history_and_aligns_cursor() -> None:
    app = _app()
    assert app is not None
    widget = EEGTraceWidget()

    widget.render(_state(752, _samples(2, 752)))

    assert widget._write_index == 2
    assert widget._samples[1] == (751.0,)
    assert widget._samples[2] is None
    assert widget._samples[3] == (3.0,)
    assert not widget._pending_samples
    assert widget._sweep_timer.interval() == 4


def test_overlapping_snapshots_enqueue_each_new_sample_once() -> None:
    _app()
    widget = EEGTraceWidget()
    widget.render(_state(3, _samples(0, 3)))

    next_state = _state(5, _samples(0, 5))
    widget.render(next_state)
    widget.render(next_state)

    assert tuple(widget._pending_samples) == ((3.0,), (4.0,))
    assert widget._sweep_timer.isActive()

    widget._advance_sweep()
    assert widget._samples[3] == (3.0,)
    assert widget._write_index == 4
    assert widget._samples[4] is None

    widget._advance_sweep()
    assert widget._samples[4] == (4.0,)
    assert widget._write_index == 5
    assert widget._samples[5] is None
    assert not widget._pending_samples
    assert not widget._sweep_timer.isActive()


def test_sweep_wraps_without_joining_across_the_display_boundary() -> None:
    _app()
    widget = EEGTraceWidget()
    widget.render(_state(749, _samples(739, 749)))
    widget.render(_state(751, _samples(1, 751)))

    widget._advance_sweep()

    assert widget._samples[749] == (749.0,)
    assert widget._write_index == 0
    assert widget._samples[0] is None

    widget._advance_sweep()

    assert widget._samples[0] == (750.0,)
    assert widget._write_index == 1
    assert widget._samples[1] is None


def test_missing_history_resynchronizes_from_latest_snapshot() -> None:
    _app()
    widget = EEGTraceWidget()
    widget.render(_state(3, _samples(0, 3)))

    widget.render(_state(800, _samples(50, 800)))

    assert widget._last_sample_count == 800
    assert widget._write_index == 50
    assert widget._samples[49] == (799.0,)
    assert widget._samples[50] is None
    assert not widget._pending_samples


def test_session_rollback_and_channel_changes_resynchronize() -> None:
    _app()
    widget = EEGTraceWidget()
    widget.render(_state(3, _samples(0, 3)))

    widget.render(_state(2, _samples(0, 2), session_id="session-b"))
    assert widget._session_id == "session-b"
    assert widget._write_index == 2
    assert widget._samples[1] == (1.0,)

    widget.render(_state(1, _samples(10, 11), session_id="session-b"))
    assert widget._last_sample_count == 1
    assert widget._write_index == 1
    assert widget._samples[0] == (10.0,)

    widget.render(_state(
        1,
        ((10.0, 20.0),),
        session_id="session-b",
        indexes=(1,),
        labels=("F4",),
    ))
    assert widget._indexes == (1,)
    assert widget._labels == ("F4",)
    assert widget._samples[0] == (10.0, 20.0)
    assert not widget._pending_samples


def test_excessive_backlog_resynchronizes_and_clear_resets_everything() -> None:
    _app()
    widget = EEGTraceWidget()
    widget.render(_state(0, ()))
    widget._pending_samples.extend(
        ((-1.0,),) * EEGTraceWidget.SWEEP_CAPACITY
    )

    widget.render(_state(1, ((0.0,),)))

    assert not widget._pending_samples
    assert widget._samples[0] == (0.0,)
    assert widget._samples[1] is None
    assert widget._write_index == 1

    widget.render(_state(2, ((0.0,), (1.0,))))
    assert widget._sweep_timer.isActive()
    widget.clear()

    assert not widget._sweep_timer.isActive()
    assert widget._last_sample_count is None
    assert widget._session_id is None
    assert widget._write_index == 0
    assert not widget._pending_samples
    assert not any(sample is not None for sample in widget._samples)
    assert widget._indexes == ()
    assert widget._labels == ()
