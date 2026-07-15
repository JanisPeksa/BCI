"""Acquisition backend selection from a resolved device profile."""

from __future__ import annotations

from imagined_speech.acquisition.base import AcquisitionBackend, AcquisitionError
from imagined_speech.acquisition.brainflow_backend import BrainFlowAcquisitionBackend
from imagined_speech.acquisition.lsl_backend import LSLAcquisitionBackend
from imagined_speech.acquisition.synthetic import SyntheticAcquisitionBackend
from imagined_speech.config import ResolvedExperiment
from imagined_speech.engine import ProtocolClock


def create_acquisition_backend(
    resolved: ResolvedExperiment, clock: ProtocolClock
) -> AcquisitionBackend:
    backend = resolved.device.backend
    if backend == "synthetic":
        return SyntheticAcquisitionBackend(resolved.device, clock)
    if backend in {"brainflow_synthetic", "cyton", "replay"}:
        return BrainFlowAcquisitionBackend(resolved.device, resolved.device_path)
    if backend == "lsl":
        return LSLAcquisitionBackend(resolved.device)
    raise AcquisitionError(f"unsupported acquisition backend: {backend}")
