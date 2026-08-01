#!/usr/bin/env python3
"""Create a target-locked SSVEP FFT plot from a converted XDF recording.

The reader is intentionally self-contained for the XDF dialect produced by
``convert_session_to_xdf.py``.  It keeps the analysis reproducible without
requiring the optional ``pyxdf`` package.
"""

from __future__ import annotations

import argparse
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def read_vuint(data: bytes, position: int) -> tuple[int, int]:
    width = data[position]
    position += 1
    if width == 1:
        return data[position], position + 1
    if width == 4:
        return struct.unpack_from("<I", data, position)[0], position + 4
    if width == 8:
        return struct.unpack_from("<Q", data, position)[0], position + 8
    raise ValueError(f"unsupported XDF variable integer width: {width}")


def parse_xdf(path: Path) -> dict[int, dict[str, Any]]:
    data = path.read_bytes()
    if data[:4] != b"XDF:":
        raise ValueError(f"not an XDF file: {path}")

    streams: dict[int, dict[str, Any]] = {}
    position = 4
    while position < len(data):
        chunk_length, position = read_vuint(data, position)
        end = position + chunk_length
        tag = struct.unpack_from("<H", data, position)[0]
        position += 2
        if tag == 2:
            stream_id = struct.unpack_from("<I", data, position)[0]
            info = ET.fromstring(data[position + 4 : end])
            desc = info.find("desc")
            description = {}
            if desc is not None:
                description = {
                    child.tag: child.text or ""
                    for child in desc
                    if child.tag != "channels"
                }
            channels = []
            if desc is not None and desc.find("channels") is not None:
                for channel in desc.find("channels"):
                    channels.append({
                        child.tag: child.text or ""
                        for child in channel
                    })
            streams[stream_id] = {
                "name": info.findtext("name", ""),
                "format": info.findtext("channel_format", ""),
                "channel_count": int(info.findtext("channel_count", "0")),
                "nominal_rate": float(info.findtext("nominal_srate", "0")),
                "description": description,
                "channels": channels,
                "timestamps": [],
                "values": [],
            }
        elif tag == 3:
            stream_id = struct.unpack_from("<I", data, position)[0]
            stream = streams[stream_id]
            cursor = position + 4
            count, cursor = read_vuint(data, cursor)
            previous_timestamp = None
            for _ in range(count):
                timestamp_width = data[cursor]
                cursor += 1
                if timestamp_width == 8:
                    timestamp = struct.unpack_from("<d", data, cursor)[0]
                    cursor += 8
                    previous_timestamp = timestamp
                elif timestamp_width == 0 and previous_timestamp is not None:
                    timestamp = previous_timestamp
                else:
                    raise ValueError("unsupported or missing XDF sample timestamp")

                channels = stream["channel_count"]
                if stream["format"] == "double64":
                    values = struct.unpack_from("<" + "d" * channels, data, cursor)
                    cursor += 8 * channels
                elif stream["format"] == "int32":
                    values = struct.unpack_from("<" + "i" * channels, data, cursor)
                    cursor += 4 * channels
                elif stream["format"] == "string":
                    value_length, cursor = read_vuint(data, cursor)
                    values = (data[cursor : cursor + value_length].decode("utf-8"),)
                    cursor += value_length
                else:
                    raise ValueError(f"unsupported XDF format: {stream['format']}")
                stream["timestamps"].append(timestamp)
                stream["values"].append(values)
        position = end

    for stream in streams.values():
        stream["timestamps"] = np.asarray(stream["timestamps"], dtype=float)
        if stream["format"] == "double64":
            stream["values"] = np.asarray(stream["values"], dtype=float)
        elif stream["format"] == "int32":
            stream["values"] = np.asarray(stream["values"], dtype=np.int32)
    return streams


def target_onsets(events: dict[str, Any], target_frequency: float) -> list[dict[str, Any]]:
    onsets = []
    for timestamp, values in zip(events["timestamps"], events["values"]):
        event = json.loads(values[0])
        if event.get("event_type") != "stimulus_onset":
            continue
        event_target = event.get("payload", {}).get("target_frequency_hz")
        if event_target is not None and abs(float(event_target) - target_frequency) < 1e-9:
            onsets.append({"timestamp": float(timestamp), "event": event})
    return onsets


def run_analysis(
    xdf_path: Path,
    output_dir: Path,
    target_frequency: float | None,
    start_offset: float,
    end_offset: float,
) -> tuple[Path, Path, dict[str, Any]]:
    streams = parse_xdf(xdf_path)
    eeg = next(stream for stream in streams.values() if stream["name"] == "Cyton EEG")
    event_stream = next(stream for stream in streams.values() if stream["name"] == "SSVEP Session Events")
    description = eeg["description"]
    if target_frequency is None:
        target_frequency = float(description["target_frequency_hz"])

    sfreq = float(eeg["nominal_rate"])
    eeg_timestamps = eeg["timestamps"]
    eeg_values = eeg["values"]
    channel_labels = [channel.get("label", f"ch{index + 1}") for index, channel in enumerate(eeg["channels"])]
    onsets = target_onsets(event_stream, target_frequency)
    if not onsets:
        raise ValueError(f"no stimulus_onset events found for target frequency {target_frequency:g} Hz")

    samples_per_trial = int(round((end_offset - start_offset) * sfreq))
    if samples_per_trial < 8:
        raise ValueError("FFT window is too short")
    nfft = max(4096, 1 << (samples_per_trial - 1).bit_length())
    window = np.hanning(samples_per_trial)
    spectra = []
    used_trials = []
    for onset in onsets:
        start_time = onset["timestamp"] + start_offset
        first = int(np.searchsorted(eeg_timestamps, start_time, side="left"))
        stop = first + samples_per_trial
        if stop > len(eeg_values):
            continue
        segment = eeg_values[first:stop].copy()
        segment -= np.mean(segment, axis=0, keepdims=True)
        segment = segment * window[:, None]
        spectrum = 2.0 * np.abs(np.fft.rfft(segment, n=nfft, axis=0)) / np.sum(window)
        spectrum[0, :] /= 2.0
        spectra.append(spectrum)
        used_trials.append({
            "timestamp": onset["timestamp"],
            "trial_id": onset["event"].get("trial_id"),
            "sample_start": first,
            "sample_count": samples_per_trial,
        })
    if not spectra:
        raise ValueError("none of the target windows fit inside the EEG recording")

    frequencies = np.fft.rfftfreq(nfft, 1.0 / sfreq)
    mean_spectrum = np.mean(np.stack(spectra, axis=0), axis=0)
    channel_mean = np.mean(mean_spectrum, axis=1)
    target_bin = int(np.argmin(np.abs(frequencies - target_frequency)))
    analysis_band = (frequencies >= 5.0) & (frequencies <= 20.0)
    peak_bin = np.where(analysis_band)[0][np.argmax(channel_mean[analysis_band])]

    output_dir.mkdir(parents=True, exist_ok=True)
    target_label = f"{target_frequency:g}".replace(".", "p")
    plot_path = output_dir / f"{xdf_path.stem}_fft_target_{target_label}Hz.png"
    report_path = output_dir / f"{xdf_path.stem}_fft_target_{target_label}Hz.json"

    plot_frequency = (frequencies >= 5.0) & (frequencies <= 20.0)
    figure, axis = plt.subplots(figsize=(11, 6.5), constrained_layout=True)
    for index, label in enumerate(channel_labels):
        axis.plot(frequencies[plot_frequency], mean_spectrum[plot_frequency, index], linewidth=1.1, label=label)
    axis.plot(
        frequencies[plot_frequency],
        channel_mean[plot_frequency],
        color="black",
        linewidth=2.6,
        label="8-channel mean",
    )
    axis.axvline(target_frequency, color="#d62728", linestyle="--", linewidth=1.8, label=f"target {target_frequency:g} Hz")
    axis.scatter([frequencies[peak_bin]], [channel_mean[peak_bin]], color="#d62728", zorder=5)
    axis.annotate(
        f"peak {frequencies[peak_bin]:.3f} Hz",
        (frequencies[peak_bin], channel_mean[peak_bin]),
        xytext=(8, 10),
        textcoords="offset points",
        color="#d62728",
    )
    axis.set(
        title=f"SSVEP target-locked FFT — {target_frequency:g} Hz target",
        xlabel="Frequency (Hz)",
        ylabel="Mean FFT magnitude (µV)",
        xlim=(5.0, 20.0),
    )
    axis.grid(alpha=0.25)
    axis.legend(ncol=3, fontsize=9)
    figure.savefig(plot_path, dpi=160)
    plt.close(figure)

    report = {
        "xdf": str(xdf_path.resolve()),
        "target_frequency_hz": target_frequency,
        "target_stimulus_id": description.get("target_stimulus_id"),
        "sampling_rate_hz": sfreq,
        "channels": channel_labels,
        "window_seconds": [start_offset, end_offset],
        "window_samples": samples_per_trial,
        "fft_length": nfft,
        "fft_bin_resolution_hz": sfreq / nfft,
        "target_bin_frequency_hz": float(frequencies[target_bin]),
        "peak_frequency_hz_5_to_20": float(frequencies[peak_bin]),
        "target_bin_mean_magnitude_uv": float(channel_mean[target_bin]),
        "peak_mean_magnitude_uv": float(channel_mean[peak_bin]),
        "target_trials_found": len(onsets),
        "target_trials_used": len(used_trials),
        "trials": used_trials,
        "plot": str(plot_path.resolve()),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return plot_path, report_path, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xdf", type=Path, help="converted SSVEP session XDF")
    parser.add_argument("--output-dir", type=Path, help="directory for PNG and JSON outputs")
    parser.add_argument("--target-frequency", type=float, help="override target frequency from XDF metadata")
    parser.add_argument("--start-offset", type=float, default=0.25)
    parser.add_argument("--end-offset", type=float, default=1.75)
    args = parser.parse_args()
    xdf_path = args.xdf.expanduser().resolve()
    output_dir = (args.output_dir or xdf_path.parent / "fft-analysis").expanduser().resolve()
    plot_path, report_path, report = run_analysis(
        xdf_path,
        output_dir,
        args.target_frequency,
        args.start_offset,
        args.end_offset,
    )
    print(json.dumps({"plot": str(plot_path), "report": str(report_path), **{key: report[key] for key in (
        "target_frequency_hz", "target_trials_found", "target_trials_used", "peak_frequency_hz_5_to_20", "target_bin_mean_magnitude_uv"
    )}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
