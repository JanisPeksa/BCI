#!/usr/bin/env python3
"""Analyze a photosensor recording against an SSVEP session.

The session may come from ssvep-bci or psychopy-ssvep. Results and a copy of
the photosensor input are written to ``<session>/photosensor-check``.

Usage:
    run-photosensor-check --session <session_dir> --light <light_amp.csv>
    run-photosensor-check --latest --session-root <sessions_dir> --light <light_amp.csv>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from photosensor_check.analysis import (  # noqa: E402
    detect_peak,
    find_latest_session,
    ideal_waveform,
    load_light_amp,
    load_session_trials,
    power_spectrum,
    retime_uniform,
    sampling_diagnostics,
    segment_trials,
    signal_quality,
    summarize,
    verify_alignment,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--session",
        help="ssvep-bci or psychopy-ssvep session folder",
    )
    source.add_argument(
        "--latest",
        action="store_true",
        help="use the newest compatible session under --session-root",
    )
    parser.add_argument(
        "--session-root",
        help="directory whose direct children are session folders (required with --latest)",
    )
    parser.add_argument(
        "--light",
        required=True,
        help="light_amp.csv produced by record-photosensor",
    )
    parser.add_argument(
        "--latency",
        type=float,
        default=0.07,
        help="serial latency added to every trial window (seconds)",
    )
    parser.add_argument(
        "--tolerance-hz",
        type=float,
        default=0.25,
        help="maximum absolute detected-frequency error for PASS",
    )
    args = parser.parse_args(argv)
    if args.latest and not args.session_root:
        parser.error("--latest requires --session-root")
    if args.session and args.session_root:
        parser.error("--session-root can only be used with --latest")
    return args


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
        ideal = mid + amp * ideal_waveform(trial, segment.t)
        ax.plot(segment.t - t0, segment.x, color="black", lw=1.2, label="measured")
        ax.plot(
            segment.t - t0,
            ideal,
            color="#e67e22",
            lw=1.0,
            alpha=0.9,
            label=f"ideal {trial.waveform}",
        )
        ax.set_xlim(0, max(float(segment.t[-1] - t0), 1e-9))
        margin = (hi - lo) * 0.06 if hi > lo else 1.0
        ax.set_ylim(lo - margin, hi + margin)
        ax.set_title(
            f"trial {trial.trial_number} {trial.stimulus_id} "
            f"target={trial.frequency_hz:.2f} Hz"
        )
        ax.set_xlabel("time (s)")
        ax.set_ylabel("light_amp")
        ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def _plot_fft_classes(segments, sfreq: float) -> plt.Figure:
    nonempty = [segment for segment in segments if len(segment.x) > 0]
    fig, ax = plt.subplots(figsize=(12, 6))
    if not nonempty:
        ax.text(0.5, 0.5, "no light data in any trial window", ha="center")
        return fig
    nfft = max(len(segment.x) for segment in nonempty)
    classes: dict[tuple[str, float, float, str], list] = {}
    for segment in nonempty:
        trial = segment.trial
        key = (
            trial.stimulus_id,
            trial.frequency_hz,
            trial.phase_offset_radians,
            trial.waveform,
        )
        freqs, psd = power_spectrum(segment.x, sfreq, nfft=nfft)
        classes.setdefault(key, []).append((freqs, psd))
    for (stimulus_id, target, _phase, waveform), spectra in sorted(classes.items()):
        freqs = spectra[0][0]
        mean = np.mean([psd for _, psd in spectra], axis=0)
        band = (freqs >= 1) & (freqs <= 40)
        ax.plot(
            freqs[band],
            mean[band],
            lw=1.0,
            label=f"{stimulus_id} ({target:.2f} Hz, {waveform})",
        )
        peak = detect_peak(freqs, mean)
        if peak is not None:
            ax.axvline(peak, ls=":", lw=0.8, alpha=0.6)
    ax.set_xlim(1, 40)
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("mean PSD")
    ax.set_title("FFT per stimulus class")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def _copy_input(source: Path, destination: Path) -> bool:
    """Copy one input unless it already is the session-local destination."""
    if source.resolve() == destination.resolve():
        return False
    shutil.copy2(source, destination)
    return True


def _run(args: argparse.Namespace) -> int:
    session = (
        find_latest_session(args.session_root)
        if args.latest
        else Path(args.session).expanduser().resolve()
    )
    light_path = Path(args.light).expanduser().resolve()
    if not light_path.is_file():
        raise ValueError(f"light data not found: {light_path}")

    print(f"session: {session}")
    trials, duration = load_session_trials(session)
    frame = load_light_amp(light_path)

    diagnostics = sampling_diagnostics(frame)
    if diagnostics["gap_count"]:
        print(
            f"note: {diagnostics['gap_count']} serial stall(s) totaling "
            f"{diagnostics['gap_seconds']:.2f}s; sample rate computed over active time only"
        )
    if diagnostics["bursty"]:
        print(
            f"note: serial reads were bursty (median-rate "
            f"{diagnostics['median_rate_hz']:.0f} Hz vs span-rate "
            f"{diagnostics['span_rate_hz']:.0f} Hz); using active-rate "
            f"{diagnostics['active_rate_hz']:.0f} Hz on a uniform timeline"
        )

    sfreq, frame = retime_uniform(frame)
    quality = signal_quality(frame["light_amp"].to_numpy())
    if not quality["has_contrast"]:
        print(
            "WARNING: light_amp has almost no contrast "
            f"(p05={quality['p05']:.0f}, p95={quality['p95']:.0f}); "
            "the sensor may be saturated or off the flickering stimulus"
        )

    segments = segment_trials(frame, trials, latency=args.latency, default_duration=duration)
    summary = summarize(segments, sfreq, tolerance_hz=args.tolerance_hz)
    alignment = verify_alignment(frame, trials)

    out = session / "photosensor-check"
    out.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out / "summary.csv", index=False)
    time_figure = _plot_time_domain(segments)
    time_figure.savefig(out / "time_domain.png", dpi=110)
    plt.close(time_figure)
    fft_figure = _plot_fft_classes(segments, sfreq)
    fft_figure.savefig(out / "fft_classes.png", dpi=110)
    plt.close(fft_figure)
    _copy_input(light_path, out / "light_amp.csv")
    sync_path = light_path.with_name("photosensor_sync.csv")
    if sync_path.is_file():
        _copy_input(sync_path, out / "photosensor_sync.csv")

    empty = int((summary["samples"] == 0).sum())
    print(f"sample rate ~ {sfreq:.0f} Hz  |  trials: {len(trials)} (empty windows: {empty})")
    median_delta = summary["delta_hz"].dropna().abs().median()
    print(f"median |delta| = {median_delta:.4f} Hz")

    def _fmt(value: float) -> str:
        return "-" if not np.isfinite(value) else f"{value:.0f}"

    print(
        "per-trial light deltas (amp_p05 -> amp_p95): "
        f"[{', '.join(_fmt(value) for value in summary['amp_p05'])}] -> "
        f"[{', '.join(_fmt(value) for value in summary['amp_p95'])}]"
    )
    passed = int(summary["pass"].sum())
    failed = int((~summary["pass"]).sum())
    print(f"PASS {passed} / FAIL {failed}")
    if failed:
        columns = ["trial_number", "stimulus_id", "target_hz", "detected_hz", "delta_hz"]
        print("failed trials:")
        print(summary.loc[~summary["pass"], columns].to_string(index=False))
    residual_spread = alignment["residual_ms"].std()
    if np.isfinite(residual_spread) and residual_spread > 5.0:
        print(
            "warning: wall-clock vs monotonic alignment residuals are spread "
            f"by {residual_spread:.1f} ms -- check clocks"
        )
    print(f"results: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _run(args)
    except (KeyError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
