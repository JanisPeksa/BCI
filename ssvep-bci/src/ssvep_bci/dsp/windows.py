from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

import numpy as np

from ssvep_bci.acquisition.base import SampleBatch
from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.dsp.contracts import StimulusWindow
from ssvep_bci.events.models import ProtocolEvent


EDGE_TOLERANCE_SECONDS = 0.025


@dataclass(frozen=True)
class WindowRequest:
    onset: ProtocolEvent
    offset: ProtocolEvent


class SampleRingBuffer:
    def __init__(self, channel_count: int, max_seconds: float, sampling_rate_hz: float) -> None:
        self.channel_count = channel_count
        self.max_samples = max(1, int(max_seconds * sampling_rate_hz))
        self._eeg = np.empty((0, channel_count), dtype=np.float64)
        self._times = np.empty((0,), dtype=np.float64)
        self._condition = threading.Condition()

    def append(self, batch: SampleBatch) -> None:
        with self._condition:
            self._eeg = np.concatenate((self._eeg, batch.eeg), axis=0)[-self.max_samples:]
            self._times = np.concatenate(
                (self._times, batch.aligned_monotonic_timestamps), axis=0
            )[-self.max_samples:]
            self._condition.notify_all()

    def extract_wait(
        self,
        start: float,
        end: float,
        expected_samples: int,
        timeout_seconds: float,
        channel_indexes: tuple[int, ...],
    ) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
        deadline = time.perf_counter() + timeout_seconds
        with self._condition:
            while (self._times.size == 0 or self._times[-1] < end) and time.perf_counter() < deadline:
                self._condition.wait(timeout=min(0.05, max(0.0, deadline - time.perf_counter())))
            mask = (
                (self._times >= start - EDGE_TOLERANCE_SECONDS)
                & (self._times < end + EDGE_TOLERANCE_SECONDS)
            )
            indexes = np.flatnonzero(mask)
            diagnostics: list[str] = []
            if indexes.size < expected_samples:
                diagnostics.append(
                    f"expected {expected_samples} samples, found {indexes.size}"
                )
                return (
                    self._eeg[indexes][:, channel_indexes].copy(),
                    self._times[indexes].copy(),
                    tuple(diagnostics),
                )
            if indexes.size > expected_samples:
                centers = self._times[indexes]
                target_center = (start + end) / 2
                best_offset = min(
                    range(indexes.size - expected_samples + 1),
                    key=lambda offset: abs(
                        (centers[offset] + centers[offset + expected_samples - 1]) / 2
                        - target_center
                    ),
                )
                indexes = indexes[best_offset:best_offset + expected_samples]
                diagnostics.append(
                    "selected centered samples from edge-tolerant timestamp window"
                )
            selected_times = self._times[indexes]
            if selected_times.size > 1 and np.any(np.diff(selected_times) <= 0):
                diagnostics.append("sample timestamps are not strictly increasing")
            return (
                self._eeg[indexes][:, channel_indexes].copy(),
                selected_times.copy(),
                tuple(diagnostics),
            )


def make_window(
    request: WindowRequest,
    resolved: ResolvedExperiment,
    ring: SampleRingBuffer,
) -> StimulusWindow:
    config = resolved.config.processing
    onset = request.onset.monotonic_timestamp
    offset = request.offset.monotonic_timestamp
    start = onset + config.window.onset_offset_seconds
    end = start + config.window.length_seconds
    expected = round(config.window.length_seconds * resolved.device.sampling_rate_hz)
    labels = [channel.label for channel in resolved.device.channels]
    channel_indexes = tuple(labels.index(label) for label in config.channels)
    eeg, times, diagnostics = ring.extract_wait(
        start,
        end,
        expected,
        config.window.wait_timeout_seconds,
        channel_indexes,
    )
    stimulus_by_id = {stimulus.id: stimulus for stimulus in resolved.config.stimuli}
    target_stimulus = stimulus_by_id.get(request.onset.stimulus_id or "")
    if target_stimulus is None:
        raise ValueError(f"unknown stimulus ID in onset event: {request.onset.stimulus_id}")
    return StimulusWindow(
        window_id=str(uuid.uuid4()),
        presentation_id=request.onset.presentation_id or "",
        trial_id=request.onset.trial_id or "",
        stimulus_id=request.onset.stimulus_id or "",
        target_frequency_hz=target_stimulus.frequency_hz,
        candidate_frequencies_hz=config.candidate_frequencies_hz,
        onset_monotonic_timestamp=onset,
        offset_monotonic_timestamp=offset,
        analysis_start_monotonic_timestamp=start,
        analysis_end_monotonic_timestamp=end,
        sampling_rate_hz=resolved.device.sampling_rate_hz,
        channel_names=config.channels,
        eeg=eeg,
        aligned_monotonic_timestamps=times,
        expected_sample_count=expected,
        diagnostics=diagnostics,
    )
