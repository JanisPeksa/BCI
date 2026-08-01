"""Shared helpers for SSVEP session photosensor frequency checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


SESSION_FILES = ("manifest.json", "events.jsonl", "experiment-config.yaml")
SUPPORTED_WAVEFORMS = {"square", "sinusoidal"}


def _parse_wall(value: str) -> float:
    """Parse an ISO-8601 UTC datetime into a Unix epoch (float seconds)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@dataclass(frozen=True)
class Trial:
    presentation_id: str
    trial_number: int | None
    stimulus_id: str
    frequency_hz: float
    phase_offset_radians: float
    duty_cycle: float
    waveform: str
    onset_monotonic: float
    onset_wall: float
    offset_monotonic: float | None
    offset_wall: float | None


@dataclass
class Segment:
    trial: Trial
    t: np.ndarray
    x: np.ndarray


def load_events(session_path: str | Path) -> list[dict[str, Any]]:
    """Parse every line of a session's events.jsonl into dicts."""
    path = Path(session_path) / "events.jsonl"
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def find_latest_session(session_root: str | Path) -> Path:
    """Return the newest session under ``session_root`` by manifest creation time.

    Both ssvep-bci applications write ``created_at_utc`` when the session folder
    is created. Session status is intentionally ignored so an active recording
    can be selected once it has emitted usable events.
    """
    root = Path(session_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"session root is not a directory: {root}")

    candidates: list[tuple[float, Path]] = []
    for path in root.iterdir():
        if not path.is_dir() or not all((path / name).is_file() for name in SESSION_FILES):
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            created_at = manifest["created_at_utc"]
            candidates.append((_parse_wall(str(created_at)), path.resolve()))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    if not candidates:
        raise ValueError(f"no usable session folders found in: {root}")
    return max(candidates, key=lambda item: (item[0], item[1].name))[1]


def load_stimuli(session_path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the shared stimulus metadata used by both SSVEP applications."""
    path = Path(session_path) / "experiment-config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("stimuli"), list):
        raise ValueError(f"invalid stimulus configuration: {path}")
    stimuli: dict[str, dict[str, Any]] = {}
    for stim in config["stimuli"]:
        waveform = str(stim.get("waveform", "square"))
        if waveform not in SUPPORTED_WAVEFORMS:
            raise ValueError(f"unsupported waveform '{waveform}' in {path}")
        stimuli[str(stim["id"])] = {
            "frequency_hz": float(stim["frequency_hz"]),
            "phase_offset_radians": float(stim.get("phase_offset_radians", 0.0)),
            "duty_cycle": float(stim.get("duty_cycle", 0.5)),
            "waveform": waveform,
        }
    return stimuli


def build_trials(
    events: list[dict[str, Any]],
    stimuli: dict[str, dict[str, Any]],
    default_duration_seconds: float = 5.0,
) -> list[Trial]:
    """Pair each frame-swap-confirmed stimulus_onset with its offset.

    Only onsets whose payload has ``confirmed_by_frame_swap`` are used, matching
    the timestamps the app considers authoritative. Offsets are matched by
    presentation_id; a missing offset (aborted trial) falls back to
    ``onset + default_duration_seconds``.
    """
    onsets: dict[str, dict[str, Any]] = {}
    offsets: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") == "stimulus_onset":
            payload = event.get("payload") or {}
            if payload.get("confirmed_by_frame_swap"):
                onsets[event["presentation_id"]] = event
        elif event.get("event_type") == "stimulus_offset":
            payload = event.get("payload") or {}
            if payload.get("confirmed_by_frame_swap"):
                offsets.setdefault(event["presentation_id"], event)

    trials: list[Trial] = []
    for presentation_id, onset in onsets.items():
        stimulus_id = onset["stimulus_id"]
        stim = stimuli[stimulus_id]
        offset = offsets.get(presentation_id)
        trials.append(Trial(
            presentation_id=presentation_id,
            trial_number=onset.get("trial_number"),
            stimulus_id=stimulus_id,
            frequency_hz=stim["frequency_hz"],
            phase_offset_radians=stim["phase_offset_radians"],
            duty_cycle=stim["duty_cycle"],
            waveform=stim["waveform"],
            onset_monotonic=onset["monotonic_timestamp"],
            onset_wall=_parse_wall(onset["wall_clock_timestamp_utc"]),
            offset_monotonic=offset["monotonic_timestamp"] if offset else None,
            offset_wall=_parse_wall(offset["wall_clock_timestamp_utc"]) if offset else None,
        ))
    trials.sort(key=lambda trial: trial.onset_monotonic)
    return trials


def load_session_trials(session_path: str | Path) -> tuple[list[Trial], float]:
    """Load frame-confirmed trials and configured duration from a session."""
    session = Path(session_path).expanduser().resolve()
    missing = [name for name in SESSION_FILES if not (session / name).is_file()]
    if missing:
        raise ValueError(
            f"not a compatible session folder ({', '.join(missing)} missing): {session}"
        )
    config = yaml.safe_load((session / "experiment-config.yaml").read_text(encoding="utf-8"))
    try:
        duration = float(config["protocol"]["stimulation_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid protocol duration in: {session}") from exc
    trials = build_trials(load_events(session), load_stimuli(session), duration)
    if not trials:
        raise ValueError(f"session has no frame-confirmed stimulus onset events: {session}")
    return trials, duration


def ideal_waveform(trial: Trial, timestamps: np.ndarray) -> np.ndarray:
    """Return the configured ideal waveform in the normalized range [-1, 1]."""
    elapsed = np.asarray(timestamps, dtype=float) - trial.onset_monotonic
    phase = 2.0 * np.pi * trial.frequency_hz * elapsed + trial.phase_offset_radians
    if trial.waveform == "sinusoidal":
        return np.sin(phase)
    cycles = np.mod(phase / (2.0 * np.pi), 1.0)
    return np.where(cycles < trial.duty_cycle, 1.0, -1.0)


def load_light_amp(path: str | Path) -> pd.DataFrame:
    """Load host timestamps, light amplitude, and optional device timing."""
    frame = pd.read_csv(path, skipinitialspace=True)
    frame["time_monotonic"] = frame["time_monotonic"].astype(float)
    frame["time_wall"] = frame["time_wall"].astype(float)
    frame["light_amp"] = frame["light_amp"].astype(float)
    for column in ("sample_index", "device_time_us"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _has_device_timing(frame: pd.DataFrame) -> bool:
    required = {"sample_index", "device_time_us"}
    return required.issubset(frame.columns) and bool(
        frame[["sample_index", "device_time_us"]].notna().all(axis=1).all()
    )


def _unwrap_uint32(values: np.ndarray, label: str) -> np.ndarray:
    """Unwrap an unsigned 32-bit Arduino counter into a monotonic float array."""
    raw = np.asarray(values, dtype=np.float64)
    if len(raw) == 0:
        return raw
    out = np.empty_like(raw)
    offset = 0.0
    out[0] = raw[0]
    for index in range(1, len(raw)):
        if raw[index] < raw[index - 1]:
            if raw[index - 1] - raw[index] < 2**31:
                raise ValueError(f"{label} moved backward without a uint32 wrap")
            offset += 2**32
        out[index] = raw[index] + offset
    if np.any(np.diff(out) <= 0):
        raise ValueError(f"{label} does not advance strictly")
    return out


def _device_timing(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Return device elapsed seconds, unwrapped indexes, rate, and missing count."""
    device_us = _unwrap_uint32(frame["device_time_us"].to_numpy(), "device_time_us")
    sample_index = _unwrap_uint32(frame["sample_index"].to_numpy(), "sample_index")
    elapsed = (device_us - device_us[0]) * 1e-6
    dt = np.diff(elapsed)
    if len(dt) == 0 or np.any(dt <= 0):
        raise ValueError("device timing has fewer than two advancing samples")
    sfreq = 1.0 / float(np.median(dt))
    index_steps = np.diff(sample_index)
    missing = int(np.maximum(index_steps - 1.0, 0.0).sum())
    return elapsed, sample_index, sfreq, missing


def _serial_gaps(time_monotonic: np.ndarray,
                 gap_threshold_seconds: float = 0.05) -> tuple[np.ndarray, int, float]:
    """Mark PC serial-read stalls (gaps) in a constant-rate Arduino recording.

    The Arduino free-runs and never pauses, while the OS serial driver buffers
    bytes and delivers them in bursts. During a stall (scheduler hiccup, USB
    queue backlog, suspend) the PC stamps no timestamps for that interval even
    though the Arduino kept sampling, so the recorded span over-counts the
    elapsed sampling time. Returns ``(mask, count, total_seconds)`` of gaps.
    """
    dt = np.diff(time_monotonic)
    mask = dt > gap_threshold_seconds
    return mask, int(mask.sum()), float(dt[mask].sum())


def calibrate_sfreq(frame: pd.DataFrame,
                    gap_threshold_seconds: float = 0.05) -> float:
    """Nominal sample rate of a free-running Arduino recording.

    Device-timestamped recordings use the median Arduino interval and are not
    affected by USB buffering or host process scheduling. Legacy recordings
    use the span-based rate ``(n - 1) / active_span`` where ``active_span``
    is the wall time the Arduino was actually sampling. The OS serial driver
    buffers bytes, so the PC read loop drains them in bursts and per-byte
    timestamps under-report intra-burst spacing; the total span is otherwise
    unbiased for the constant-rate sampler. PC read stalls (gaps longer than
    ``gap_threshold_seconds``) add no samples yet inflate the span, so they are
    excluded -- otherwise the rate is biased low and every measured frequency
    scales down with it.
    """
    if _has_device_timing(frame):
        return _device_timing(frame)[2]

    time_monotonic = frame["time_monotonic"].to_numpy()
    if len(time_monotonic) < 2:
        raise ValueError("light_amp.csv has fewer than two samples")
    span = time_monotonic[-1] - time_monotonic[0]
    if span <= 0:
        raise ValueError("light_amp.csv timestamps do not advance")
    _, n_gaps, gap_seconds = _serial_gaps(time_monotonic, gap_threshold_seconds)
    active_span = span - gap_seconds
    if active_span <= 0:
        raise ValueError("entire recording falls inside serial stalls")
    return (len(time_monotonic) - 1) / active_span


def sampling_diagnostics(
    frame: pd.DataFrame,
    gap_threshold_seconds: float = 0.05,
) -> dict[str, float | bool | int | str]:
    """Report raw-vs-uniform rate estimates to detect bursty serial reads."""
    time_monotonic = frame["time_monotonic"].to_numpy()
    n = len(time_monotonic)
    if n < 2:
        return {"n": n, "span_seconds": 0.0, "median_rate_hz": 0.0,
                "span_rate_hz": 0.0, "gap_count": 0, "gap_seconds": 0.0,
                "bursty": False, "timing_source": "host",
                "device_rate_hz": 0.0, "missing_device_samples": 0}
    dt = np.diff(time_monotonic)
    span = time_monotonic[-1] - time_monotonic[0]
    _, n_gaps, gap_seconds = _serial_gaps(time_monotonic, gap_threshold_seconds)
    median_rate = 1.0 / float(np.median(dt))
    span_rate = (n - 1) / span if span > 0 else 0.0
    active_span = max(span - gap_seconds, 1e-9)
    active_rate = (n - 1) / active_span
    ratio = median_rate / span_rate if span_rate > 0 else 1.0
    result: dict[str, float | bool | int | str] = {
        "n": n,
        "span_seconds": float(span),
        "median_rate_hz": float(median_rate),
        "span_rate_hz": float(span_rate),
        "active_rate_hz": float(active_rate),
        "gap_count": int(n_gaps),
        "gap_seconds": float(gap_seconds),
        "bursty": bool(ratio > 1.5),
        "timing_source": "host",
        "device_rate_hz": 0.0,
        "missing_device_samples": 0,
    }
    if _has_device_timing(frame):
        _, _, device_rate, missing = _device_timing(frame)
        result.update({
            "timing_source": "device",
            "device_rate_hz": float(device_rate),
            "missing_device_samples": missing,
        })
    return result


def retime_uniform(frame: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """Re-time a free-running recording onto a uniform timeline.

    The Arduino samples at a near-constant rate but the PC reads in bursts, so
    the raw timestamps are locally compressed. Restoring ``t_k = t_0 + k / sfreq``
    from the span-based rate (with PC-read stalls excluded) yields the true
    per-sample instants, which keeps FFTs and trial segmentation valid
    regardless of read burstiness.

    Returns ``(sfreq, frame)`` where both time columns lie on the same uniform
    grid and a ``sample_index`` column is added.
    """
    time_monotonic = frame["time_monotonic"].to_numpy()
    n = len(time_monotonic)
    if n < 2:
        raise ValueError("light_amp.csv has fewer than two samples")
    if time_monotonic[-1] <= time_monotonic[0]:
        raise ValueError("light_amp.csv timestamps do not advance")
    if _has_device_timing(frame):
        elapsed, _, sfreq, _ = _device_timing(frame)
        uniform_elapsed = np.arange(
            int(np.floor(elapsed[-1] * sfreq)) + 1,
            dtype=float,
        ) / sfreq
        light_amp = np.interp(
            uniform_elapsed,
            elapsed,
            frame["light_amp"].to_numpy(),
        )
        # Receipt time minus device elapsed consists of a fixed clock offset
        # plus non-negative serial/USB queueing delay. A low percentile selects
        # the least-backlogged observations without trusting one outlier.
        clock_offset = float(np.quantile(time_monotonic - elapsed, 0.01))
        mapped_monotonic = clock_offset + uniform_elapsed
        wall_minus_monotonic = frame["time_wall"].to_numpy() - time_monotonic
        wall_offset = float(np.median(wall_minus_monotonic))
        return sfreq, pd.DataFrame({
            "time_monotonic": mapped_monotonic,
            "time_wall": mapped_monotonic + wall_offset,
            "light_amp": light_amp,
            "sample_index": np.arange(len(uniform_elapsed)),
            "device_time_us": uniform_elapsed * 1e6,
        })

    sfreq = calibrate_sfreq(frame)
    out = frame.copy()
    index = np.arange(n)
    out["time_monotonic"] = time_monotonic[0] + index / sfreq
    time_wall = frame["time_wall"].to_numpy()
    out["time_wall"] = time_wall[0] + index / sfreq
    out["sample_index"] = index
    return sfreq, out


def segment_trials(
    frame: pd.DataFrame,
    trials: list[Trial],
    latency: float = 0.07,
    default_duration: float = 5.0,
) -> list[Segment]:
    """Cut one light waveform per trial using absolute monotonic times.

    ``latency`` accounts for serial latency between the stimulus onset on screen
    and the PC timestamping the light data that reflects it (the same fudge Oz
    Speller used). The window is ``[onset, offset] + latency`` in the recorder's
    ``time_monotonic`` domain, which is the same clock as events.jsonl.
    """
    time_monotonic = frame["time_monotonic"].to_numpy()
    light_amp = frame["light_amp"].to_numpy()
    segments: list[Segment] = []
    for trial in trials:
        end_monotonic = (
            trial.offset_monotonic
            if trial.offset_monotonic is not None
            else trial.onset_monotonic + default_duration
        )
        start = trial.onset_monotonic + latency
        end = end_monotonic + latency
        mask = (time_monotonic >= start) & (time_monotonic <= end)
        segments.append(Segment(
            trial=trial,
            t=time_monotonic[mask].copy(),
            x=light_amp[mask].copy(),
        ))
    return segments


def verify_alignment(frame: pd.DataFrame, trials: list[Trial]) -> pd.DataFrame:
    """Cross-check monotonic and wall-clock alignment for every trial onset.

    Each trial onset has a wall-UTC timestamp in events.jsonl; the light data
    carries its own wall-UTC column. The wall-clock derived sample index should
    land at ``onset_monotonic + latency`` (residual ~ the serial latency). A
    consistent residual across trials confirms both sides share one time base.
    """
    time_monotonic = frame["time_monotonic"].to_numpy()
    time_wall = frame["time_wall"].to_numpy()
    rows = []
    for trial in trials:
        index = int(np.argmin(np.abs(time_wall - trial.onset_wall)))
        rows.append({
            "trial_number": trial.trial_number,
            "presentation_id": trial.presentation_id,
            "onset_monotonic": trial.onset_monotonic,
            "wall_mapped_monotonic": time_monotonic[index],
            "residual_s": time_monotonic[index] - trial.onset_monotonic,
        })
    out = pd.DataFrame(rows)
    out["residual_ms"] = out["residual_s"] * 1000.0
    return out


def power_spectrum(
    x: np.ndarray,
    sfreq: float,
    window: str = "hann",
    nfft: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs, PSD) for a trial's light waveform.

    ``nfft`` zero-pads all windows to a common length so PSDs from windows of
    different sizes share one frequency axis and can be averaged.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n == 0:
        return np.array([]), np.array([])
    length = n if nfft is None else max(nfft, n)
    x = x - np.mean(x)
    if window == "hann":
        x = x * np.hanning(n)
    elif window == "blackman":
        x = x * np.blackman(n)
    if length > n:
        x = np.pad(x, (0, length - n))
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(length, 1.0 / sfreq)
    return freqs, np.abs(spectrum) ** 2


def detect_peak(freqs: np.ndarray, psd: np.ndarray, lo: float = 1.0, hi: float = 40.0) -> float | None:
    """Frequency of the largest spectral peak inside [lo, hi] Hz."""
    if len(freqs) == 0:
        return None
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return None
    return float(freqs[band][np.argmax(psd[band])])


def check_harmonics(
    freqs: np.ndarray,
    psd: np.ndarray,
    target: float,
    harmonics: tuple[int, ...] = (3, 5),
    ratio: float = 0.3,
) -> tuple[int, dict[int, bool]]:
    """Return the fundamental bin and which odd harmonics clear ``ratio``.

    Compared on amplitude (sqrt of PSD): a square wave's 3rd harmonic sits at
    1/3 the fundamental amplitude and its 5th at 1/5, so ``ratio=0.3`` flags the
    presence of the 3rd harmonic while the 5th stays just under.
    """
    if len(freqs) == 0:
        return -1, {}
    amplitude = np.sqrt(psd)
    fund = int(np.argmin(np.abs(freqs - target)))
    present: dict[int, bool] = {}
    for harmonic in harmonics:
        near = int(np.argmin(np.abs(freqs - target * harmonic)))
        present[harmonic] = bool(amplitude[near] > ratio * amplitude[fund])
    return fund, present


def _run_lengths(values: np.ndarray) -> np.ndarray:
    """Lengths of consecutive equal runs in a (binary) array."""
    idx = np.flatnonzero(np.diff(values))
    starts = np.concatenate([[0], idx + 1])
    ends = np.concatenate([idx + 1, [len(values)]])
    return ends - starts


def signal_quality(x: np.ndarray) -> dict[str, float | bool]:
    """Check that the light trace has usable on/off contrast.

    A saturated or off-target sensor shows almost no dynamic range; the analysis
    then has no flicker to find, regardless of how the clocks line up.
    """
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return {"dynamic_range": 0.0, "p05": 0.0, "p95": 0.0, "has_contrast": False}
    lo = float(np.percentile(x, 5))
    hi = float(np.percentile(x, 95))
    # ~4 % of the 10-bit (0-1023) ADC range, matching the old 10/255 for 8-bit.
    has_contrast = bool(hi - lo >= 40.0)
    return {
        "dynamic_range": hi - lo,
        "p05": lo,
        "p95": hi,
        "has_contrast": has_contrast,
    }


def detect_frame_skips(
    x: np.ndarray,
    sfreq: float,
    frequency_hz: float,
    threshold: float | None = None,
    width_ratio: float = 1.5,
) -> dict[str, Any]:
    """Count pulse-width anomalies in a square-wave light trace.

    A frame-skip shows up as one on/off half-cycle roughly twice as long as the
    others. ``width_ratio`` marks a run as a skip when it exceeds that fraction
    of the nominal half-cycle width.
    """
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return {"on": np.array([]), "widths": np.array([]),
                "nominal_width": 0.0, "skips": 0, "transitions": 0}
    if threshold is None:
        lo = np.percentile(x, 20)
        hi = np.percentile(x, 80)
        threshold = 0.5 * (lo + hi)
    on = x > threshold
    widths = _run_lengths(on.astype(int))
    nominal = sfreq / frequency_hz / 2.0
    skips = int(np.sum(widths > width_ratio * nominal))
    return {
        "on": on,
        "widths": widths,
        "nominal_width": float(nominal),
        "skips": skips,
        "transitions": int(len(widths) - 1),
    }


def summarize(
    segments: list[Segment],
    sfreq: float,
    tolerance_hz: float = 0.25,
    band: tuple[float, float] = (1.0, 40.0),
) -> pd.DataFrame:
    """Per-trial table: target vs detected frequency, delta, frame-skips."""
    rows = []
    for segment in segments:
        trial = segment.trial
        freqs, psd = power_spectrum(segment.x, sfreq)
        peak = detect_peak(freqs, psd, *band)
        _, harmonics = check_harmonics(freqs, psd, trial.frequency_hz)
        skip = detect_frame_skips(segment.x, sfreq, trial.frequency_hz)
        x = segment.x
        if len(x):
            amp_p05, amp_p95 = np.percentile(x, (5, 95))
        else:
            amp_p05 = amp_p95 = float("nan")
        rows.append({
            "trial_number": trial.trial_number,
            "presentation_id": trial.presentation_id,
            "stimulus_id": trial.stimulus_id,
            "waveform": trial.waveform,
            "target_hz": trial.frequency_hz,
            "detected_hz": peak,
            "delta_hz": None if peak is None else peak - trial.frequency_hz,
            "amp_p05": float(amp_p05),
            "amp_p95": float(amp_p95),
            "amp_delta": float(amp_p95 - amp_p05),
            "samples": int(len(segment.x)),
            "duration_s": float(segment.t[-1] - segment.t[0]) if len(segment.t) else 0.0,
            "frame_skips": skip["skips"],
            "harmonic_3f": harmonics.get(3),
            "harmonic_5f": harmonics.get(5),
        })
    out = pd.DataFrame(rows)
    out["pass"] = out["delta_hz"].fillna(float("inf")).abs() <= tolerance_hz
    return out
