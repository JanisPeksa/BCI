"""EEG acquisition backends and recording orchestration."""

from imagined_speech.acquisition.base import (
    AcquisitionBackend,
    AcquisitionError,
    SampleBatch,
)
from imagined_speech.acquisition.factory import create_acquisition_backend
from imagined_speech.acquisition.recording import AcquisitionRecorder

__all__ = [
    "AcquisitionBackend",
    "AcquisitionError",
    "AcquisitionRecorder",
    "SampleBatch",
    "create_acquisition_backend",
]
