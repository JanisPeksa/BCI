from __future__ import annotations

import time

import numpy as np

from ssvep_bci.acquisition.base import AcquisitionDescriptor, SampleBatch
from ssvep_bci.config import load_experiment
from ssvep_bci.planning import compile_session_plan
from ssvep_bci.recording import SessionRecorder, validate_session


def test_session_writes_required_raw_format(tmp_path) -> None:
    resolved = load_experiment()
    resolved = resolved.__class__(
        config=resolved.config,
        config_path=resolved.config_path,
        device=resolved.device,
        device_path=resolved.device_path,
        output_root=tmp_path,
        assets=resolved.assets,
        classifier_path=resolved.classifier_path,
    )
    recorder = SessionRecorder(resolved, compile_session_plan(resolved.config), "TEST001")
    recorder.set_acquisition_descriptor(AcquisitionDescriptor(
        backend="synthetic",
        profile_id=resolved.device.profile_id,
        sampling_rate_hz=250,
        channels=resolved.device.channels,
        supports_embedded_markers=True,
    ))
    timestamps = np.arange(10, dtype=float) / 250
    recorder.record_batch(SampleBatch(
        eeg=np.zeros((10, 8)),
        source_timestamps=timestamps.copy(),
        corrected_source_timestamps=timestamps.copy(),
        aligned_monotonic_timestamps=timestamps.copy(),
        embedded_markers=np.zeros(10),
        receipt_monotonic_timestamp=1.0,
        receipt_wall_clock_timestamp_utc=recorder.created_at,
    ))
    recorder.finalize("incomplete")
    report = validate_session(recorder.path)
    assert report["sample_count"] == 10
    assert report["status"] == "incomplete"
