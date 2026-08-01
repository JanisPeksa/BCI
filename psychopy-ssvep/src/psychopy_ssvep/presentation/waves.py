"""Waveform generation for PsychoPy flicker presentation.

Both generators are frame-indexed (one value per display frame), matching
``phychopy-test/scripts/flicker_square.py``. Values are returned in PsychoPy
RGB space ``[-1, 1]``.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from psychopy_ssvep.config.models import StimulusConfig, Waveform


def generate_wave(
    frequency_hz: float,
    waveform: Waveform,
    refresh_rate: float,
    n_frames: int,
    phase_offset_radians: float = 0.0,
    duty_cycle: float = 0.5,
    gamma: float = 1.0,
) -> np.ndarray:
    """Generate one frame-indexed flicker wave in PsychoPy RGB ``[-1, 1]``.

    * ``waveform == "square"``: ``scipy.signal.square`` binarized to ``-1/+1``.
    * ``waveform == "sinusoidal"``: ``0.5 + 0.5*sin(2*pi*f*t + phase)``,
      optionally gamma-corrected, mapped to ``[-1, 1]``.
    """
    if n_frames <= 0:
        raise ValueError("n_frames must be positive")
    t = np.arange(n_frames) / refresh_rate
    if waveform == Waveform.SQUARE:
        return signal.square(
            2 * np.pi * frequency_hz * t + phase_offset_radians,
            duty=duty_cycle,
        )
    norm_sin = 0.5 + 0.5 * np.sin(2 * np.pi * frequency_hz * t + phase_offset_radians)
    if gamma != 1.0 and gamma > 0:
        norm_sin = norm_sin ** (1.0 / gamma)
    return (norm_sin * 2.0) - 1.0


def wave_for_stimulus(
    stimulus: StimulusConfig,
    refresh_rate: float,
    n_frames: int,
    gamma: float,
) -> np.ndarray:
    return generate_wave(
        frequency_hz=stimulus.frequency_hz,
        waveform=stimulus.waveform,
        refresh_rate=refresh_rate,
        n_frames=n_frames,
        phase_offset_radians=stimulus.phase_offset_radians,
        duty_cycle=stimulus.duty_cycle,
        gamma=gamma,
    )


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert ``#RRGGBB`` / ``#RRGGBBAA`` to PsychoPy RGB ``[-1, 1]``."""
    value = hex_color.lstrip("#")
    if len(value) == 8:
        value = value[:6]
    if len(value) != 6:
        raise ValueError(f"unsupported color: {hex_color}")
    return tuple((int(value[i : i + 2], 16) / 127.5) - 1.0 for i in (0, 2, 4))  # type: ignore[return-value]


def interpolate_color(
    off_rgb: tuple[float, float, float],
    on_rgb: tuple[float, float, float],
    value: float,
) -> tuple[float, float, float]:
    """Linear blend ``off -> on`` by normalized value ``(v + 1) / 2``."""
    ratio = max(0.0, min(1.0, (value + 1.0) / 2.0))
    return tuple(off + (on - off) * ratio for off, on in zip(off_rgb, on_rgb, strict=True))  # type: ignore[return-value]
