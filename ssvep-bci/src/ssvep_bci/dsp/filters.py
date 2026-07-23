from __future__ import annotations

import numpy as np
from scipy import signal


PASS_LOW = (6, 14, 22, 30, 38, 46, 54, 62, 70, 78)
STOP_LOW = (4, 10, 16, 24, 32, 40, 48, 56, 64, 72)


def notch_filter(
    eeg: np.ndarray, sampling_rate_hz: float, frequency_hz: float, quality_factor: float
) -> np.ndarray:
    b, a = signal.iirnotch(frequency_hz, quality_factor, sampling_rate_hz)
    return signal.filtfilt(b, a, eeg, axis=0)


def legacy_filter_bank(
    eeg: np.ndarray, sampling_rate_hz: float, subband_index: int
) -> np.ndarray:
    nyquist = sampling_rate_hz / 2
    wp = [PASS_LOW[subband_index] / nyquist, 90 / nyquist]
    ws = [STOP_LOW[subband_index] / nyquist, 100 / nyquist]
    order, wn = signal.cheb1ord(wp, ws, 3, 40)
    b, a = signal.cheby1(order, 0.5, wn, btype="bandpass")
    padlen = 3 * (max(len(b), len(a)) - 1)
    return signal.filtfilt(b, a, eeg, axis=0, padtype="odd", padlen=padlen)


def bandpass(eeg: np.ndarray, sampling_rate_hz: float, low: float, high: float) -> np.ndarray:
    b, a = signal.butter(4, [low, high], btype="bandpass", fs=sampling_rate_hz)
    return signal.filtfilt(b, a, eeg, axis=0)

