from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from ssvep_bci.acquisition.base import MarkerRequest
from ssvep_bci.acquisition.synthetic import SyntheticBackend
from ssvep_bci.config import load_experiment
from ssvep_bci.runtime.clock import VirtualClock


def test_synthetic_backend_is_deterministic_and_embeds_markers() -> None:
    resolved = load_experiment()
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    backend = SyntheticBackend(resolved.device, clock)
    descriptor = backend.prepare()
    backend.start()
    request = MarkerRequest(
        event_id="onset",
        event_sequence=1,
        event_type="stimulus_onset",
        marker_code=1000,
        event_monotonic_timestamp=0.5,
        event_wall_clock_timestamp_utc=clock.wall_time_utc(),
        stimulus_frequency_hz=12.75,
    )
    backend.insert_marker(request)
    clock.advance(1.0)
    batch = backend.read_available(1000)
    assert batch is not None
    assert batch.eeg.shape == (250, 8)
    assert descriptor.supports_embedded_markers
    assert batch.embedded_markers is not None
    assert 1000 in batch.embedded_markers
    assert np.all(np.diff(batch.aligned_monotonic_timestamps) > 0)

