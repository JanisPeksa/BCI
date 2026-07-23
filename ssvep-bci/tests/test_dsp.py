from __future__ import annotations

import numpy as np

from ssvep_bci.config import load_experiment
from ssvep_bci.dsp.contracts import ProcessingStatus, StimulusWindow
from ssvep_bci.dsp.processors import FbccaProcessor


def test_fbcca_selects_strong_synthetic_candidate() -> None:
    resolved = load_experiment()
    config = resolved.config.processing
    sample_count = round(config.window.length_seconds * resolved.device.sampling_rate_hz)
    t = np.arange(sample_count) / resolved.device.sampling_rate_hz
    target = 12.75
    base = np.sin(2 * np.pi * target * t) + 0.35 * np.sin(2 * np.pi * 2 * target * t)
    eeg = np.column_stack([base * (1 + channel * 0.05) for channel in range(8)])
    window = StimulusWindow(
        window_id="window",
        presentation_id="presentation",
        trial_id="trial",
        stimulus_id="target-1",
        target_frequency_hz=target,
        candidate_frequencies_hz=config.candidate_frequencies_hz,
        onset_monotonic_timestamp=1.0,
        offset_monotonic_timestamp=6.0,
        analysis_start_monotonic_timestamp=1.25,
        analysis_end_monotonic_timestamp=5.75,
        sampling_rate_hz=resolved.device.sampling_rate_hz,
        channel_names=config.channels,
        eeg=eeg,
        aligned_monotonic_timestamps=1.25 + t,
        expected_sample_count=sample_count,
    )
    result = FbccaProcessor(resolved).process(window)
    assert result.status == ProcessingStatus.PROCESSED
    assert result.predicted_frequency_hz == target
    assert len(result.correlations) == 10

