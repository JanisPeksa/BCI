#!/usr/bin/env python3
"""Headless photosensor frequency check for an ssvep-bci session.

Combines a recorded session folder (events.jsonl + experiment-config.yaml) with
the light data captured by record_photosensor.py and writes:

    summary.csv            per-trial target/detected frequency table
    time_domain.png        per-trial waveform strips (frame-skip inspection)
    fft_classes.png        FFT overlay per (frequency, phase) class

Usage:
    python run_check.py --session <session_dir> --light <light_amp.csv>
        [--latency 0.07] [--out <report_dir>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

from photosensor_check.analysis import (  # noqa: E402
    build_trials,
    detect_peak,
    load_events,
    load_light_amp,
    load_stimuli,
    power_spectrum,
    retime_uniform,
    sampling_diagnostics,
    segment_trials,
    signal_quality,
    summarize,
    verify_alignment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--session", required=True, help="path to an ssvep-bci session folder")
    parser.add_argument("--light", required=True, help="path to light_amp.csv from record_photosensor.py")
    parser.add_argument("--latency", type=float, default=0.07,
                        help="serial-latency fudge added to every trial window (s)")
    parser.add_argument("--out", default=".",
                        help="directory for summary.csv and figures")
    parser.add_argument("--tolerance-hz", type=float, default=0.25,
                        help="max |detected - target| for a PASS")
    return parser.parse_args()


def _plot_time_domain(segments) -> plt.Figure:
    n = max(len(segments), 1)
    fig, axes = plt.subplots(n, 1, figsize=(18, 1.5 * n), squeeze=False)
    for ax, segment in zip(axes[:, 0], segments):
        trial = segment.trial
        if len(segment.t) == 0:
            ax.set_title(f"trial {trial.trial_number} {trial.stimulus_id} -- NO DATA")
            continue
        t0 = segment.t[0]
        lo = float(segment.x.min())
        hi = float(segment.x.max())
        mid = 0.5 * (lo + hi)
        amp = 0.5 * (hi - lo)
        ideal = mid + amp * np.sin(2 * np.pi * trial.frequency_hz
                                   * (segment.t - trial.onset_monotonic))
        ax.plot(segment.t - t0, segment.x, color="black", lw=1.2,
                label="measured")
        ax.plot(segment.t - t0, ideal, color="#e67e22", lw=1.0, alpha=0.9,
                label="ideal")
        ax.set_xlim(0, segment.t[-1] - t0)
        margin = (hi - lo) * 0.06 if hi > lo else 1.0
        ax.set_ylim(lo - margin, hi + margin)
        ax.set_title(f"trial {trial.trial_number} {trial.stimulus_id} "
                     f"target={trial.frequency_hz:.2f} Hz")
        ax.set_xlabel("time (s)")
        ax.set_ylabel("light_amp")
        ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def _plot_fft_classes(segments, sfreq: float) -> plt.Figure:
    nonempty = [s for s in segments if len(s.x) > 0]
    fig, ax = plt.subplots(figsize=(12, 6))
    if not nonempty:
        ax.text(0.5, 0.5, "no light data in any trial window", ha="center")
        return fig
    nfft = max(len(s.x) for s in nonempty)
    classes: dict[tuple[str, float, float], list] = {}
    for segment in nonempty:
        trial = segment.trial
        key = (trial.stimulus_id, trial.frequency_hz, trial.phase_offset_radians)
        freqs, psd = power_spectrum(segment.x, sfreq, nfft=nfft)
        classes.setdefault(key, []).append((freqs, psd))
    for (stimulus_id, target, phase), spectra in sorted(classes.items()):
        freqs = spectra[0][0]
        mean = np.mean([p for _, p in spectra], axis=0)
        band = (freqs >= 1) & (freqs <= 40)
        ax.plot(freqs[band], mean[band], lw=1.0,
                label=f"{stimulus_id} ({target:.2f} Hz)")
        peak = detect_peak(freqs, mean)
        if peak is not None:
            ax.axvline(peak, ls=":", lw=0.8, alpha=0.6)
    ax.set_xlim(1, 40)
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("mean PSD")
    ax.set_title("FFT per (frequency, phase) class")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def main() -> int:
    args = parse_args()
    session = Path(args.session)
    if not (session / "events.jsonl").is_file():
        sys.exit(f"error: {session} is not a session folder (no events.jsonl)")
    if not Path(args.light).is_file():
        sys.exit(f"error: light data not found: {args.light}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    events = load_events(session)
    stimuli = load_stimuli(session)
    config = yaml.safe_load((session / "experiment-config.yaml").read_text(encoding="utf-8"))
    duration = float((config.get("protocol") or {}).get("stimulation_seconds", 5.0))
    trials = build_trials(events, stimuli, default_duration_seconds=duration)
    frame = load_light_amp(args.light)

    diagnostics = sampling_diagnostics(frame)
    if diagnostics["gap_count"]:
        print(f"note: {diagnostics['gap_count']} serial stall(s) totaling "
              f"{diagnostics['gap_seconds']:.2f}s; sample rate computed over "
              f"active time only")
    if diagnostics["bursty"]:
        print(f"note: serial reads were bursty (median-rate "
              f"{diagnostics['median_rate_hz']:.0f} Hz vs span-rate "
              f"{diagnostics['span_rate_hz']:.0f} Hz); using span-rate "
              f"{diagnostics['active_rate_hz']:.0f} Hz on a uniform timeline")

    sfreq, frame = retime_uniform(frame)

    quality = signal_quality(frame["light_amp"].to_numpy())
    if not quality["has_contrast"]:
        print("WARNING: light_amp has almost no contrast "
              f"(p05={quality['p05']:.0f}, p95={quality['p95']:.0f}); "
              "the sensor may be saturated or not on the flickering stimulus")

    segments = segment_trials(frame, trials, latency=args.latency,
                              default_duration=duration)
    summary = summarize(segments, sfreq, tolerance_hz=args.tolerance_hz)
    alignment = verify_alignment(frame, trials)

    summary.to_csv(out / "summary.csv", index=False)
    _plot_time_domain(segments).savefig(out / "time_domain.png", dpi=110)
    _plot_fft_classes(segments, sfreq).savefig(out / "fft_classes.png", dpi=110)

    empty = int((summary["samples"] == 0).sum())
    print(f"sample rate ~ {sfreq:.0f} Hz  |  trials: {len(trials)}  "
          f"(empty windows: {empty})")
    print(f"median |delta| = {summary['delta_hz'].dropna().abs().median():.4f} Hz")
    if "amp_delta" in summary:
        print("per-trial light deltas (amp_p05 -> amp_p95): "
              f"{summary['amp_p05'].round(0).astype(int).tolist()} -> "
              f"{summary['amp_p95'].round(0).astype(int).tolist()}")
    passed = int(summary["pass"].sum())
    failed = int((~summary["pass"]).sum())
    print(f"PASS {passed} / FAIL {failed}")
    if failed:
        bad = summary.loc[~summary["pass"],
                          ["trial_number", "stimulus_id", "target_hz",
                           "detected_hz", "delta_hz"]]
        print("failed trials:")
        print(bad.to_string(index=False))
    if alignment["residual_ms"].std() > 5.0:
        print("warning: wall-clock vs monotonic alignment residuals are spread "
              f"by {alignment['residual_ms'].std():.1f} ms -- check clocks")
    print(f"wrote {out / 'summary.csv'}, {out / 'time_domain.png'}, "
          f"{out / 'fft_classes.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
