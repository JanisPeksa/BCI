#!/usr/bin/env python3
"""
Convert online-interface EEG CSVs to offline-collection format for training.

Online CSV (SSVEP-Interface/online_data/eeg_*.csv):
  - No header row
  - 8 EEG columns + 1 BrainFlow timestamp column (unix seconds)
  - No Frequency / Color Code labels

Offline CSV (SSVEP-Data-Collection/demo_data/*.csv):
  - Columns: time, CH1..CH8, Color Code, Frequency
  - Frequency labels mark the START of each labeled trial segment
    (required by eeg_ai_layer.models.train.split_trials)

Because online recordings do not store stimulus labels, use either:
  --segments   explicit timestamp -> frequency mapping
  --random-labels   synthetic trial labels at fixed time windows (for testing only)

Accepted training frequencies (8-stim / FBCCA / online UI defaults):
  8.25, 8.75, 9.75, 10.75, 11.75, 12.75, 13.75, 14.25

Usage:
  # Random synthetic labels (testing / pipeline check — not real SSVEP ground truth)
  python convert_online_to_offline.py \\
    --input SSVEP-Interface/online_data/eeg_1.csv \\
    --random-labels \\
    --segment-seconds 5 \\
    --output SSVEP-Data-Collection/demo_data/converted_eeg_1.csv

  # Convert every online CSV in a folder
  python convert_online_to_offline.py \\
    --input SSVEP-Interface/online_data \\
    --output-dir SSVEP-Data-Collection/demo_data/converted
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import random
import sys
from typing import List, Optional

import numpy as np
import pandas as pd

OFFLINE_COLUMNS = ["time"] + [f"CH{i}" for i in range(1, 9)] + ["Color Code", "Frequency"]
NUM_EEG_CHANNELS = 8

# Matches SSVEP-Data-Collection/configs.py (NUM_STIMS=8), FBCCA default, and UI_DEFS.
TRAINING_FREQUENCIES = [8.25, 8.75, 9.75, 10.75, 11.75, 12.75, 13.75, 14.25]
COLOR_CODES = [f"{i:02d}" for i in range(1, 9)]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert online-interface EEG CSV to offline-collection format."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Online CSV file or directory of eeg_*.csv files",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path (single-file mode)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory (batch mode when --input is a directory)",
    )
    parser.add_argument(
        "--segments",
        help="CSV with timestamp,frequency[,color_code] labels for training",
    )
    parser.add_argument(
        "--random-labels",
        action="store_true",
        help="Assign random valid SSVEP frequencies at fixed segment boundaries",
    )
    parser.add_argument(
        "--segment-seconds",
        type=float,
        default=5.0,
        help="Length of each randomly labeled segment in seconds (default: 5)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=250,
        help="EEG sample rate in Hz for segment sizing (default: 250)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for --random-labels (default: 42)",
    )
    parser.add_argument(
        "--frequencies",
        type=str,
        default="",
        help="Comma-separated frequency list override (default: 8-stim training set)",
    )
    parser.add_argument(
        "--num-channels",
        type=int,
        default=NUM_EEG_CHANNELS,
        help="Number of EEG channels before the timestamp column (default: 8)",
    )
    return parser.parse_args()


def load_online_csv(path: str, num_channels: int) -> pd.DataFrame:
    """Load a headerless online CSV into a normalized frame."""
    df = pd.read_csv(path, header=None)
    expected_cols = num_channels + 1

    if df.shape[1] < expected_cols:
        raise ValueError(
            f"{path}: expected at least {expected_cols} columns "
            f"({num_channels} EEG + timestamp), got {df.shape[1]}"
        )

    # If the file already looks like offline format, pass it through.
    if df.shape[1] >= len(OFFLINE_COLUMNS):
        first_row = df.iloc[0].astype(str).str.lower().tolist()
        if first_row[0] == "time" and "frequency" in first_row:
            print(f"{path}: already offline format, copying as-is")
            named = pd.read_csv(path)
            return _ensure_offline_columns(named)

    eeg = df.iloc[:, :num_channels].apply(pd.to_numeric, errors="coerce")
    timestamps = pd.to_numeric(df.iloc[:, num_channels], errors="coerce")

    if eeg.isna().any().any() or timestamps.isna().any():
        raise ValueError(f"{path}: non-numeric EEG or timestamp values found")

    out = pd.DataFrame()
    out["time"] = [
        datetime.datetime.fromtimestamp(float(ts)) for ts in timestamps
    ]
    for i in range(num_channels):
        out[f"CH{i + 1}"] = eeg.iloc[:, i].values
    out["Color Code"] = np.nan
    out["Frequency"] = np.nan
    out["_timestamp"] = timestamps.values  # internal helper for segment matching
    return out


def _ensure_offline_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in OFFLINE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Offline CSV missing columns: {missing}")
    out = df[OFFLINE_COLUMNS].copy()
    if "_timestamp" not in out.columns and "time" in out.columns:
        out["_timestamp"] = pd.to_datetime(out["time"]).map(datetime.datetime.timestamp)
    return out


def load_segments(path: str) -> pd.DataFrame:
    seg = pd.read_csv(path)
    required = {"timestamp", "frequency"}
    if not required.issubset(seg.columns):
        raise ValueError(
            f"Segments file must contain columns: {sorted(required)}; got {list(seg.columns)}"
        )
    seg = seg.sort_values("timestamp").reset_index(drop=True)
    seg["timestamp"] = pd.to_numeric(seg["timestamp"], errors="coerce")
    if seg["timestamp"].isna().any():
        raise ValueError("Segments file contains invalid timestamp values")
    return seg


def parse_frequency_list(freq_arg: str) -> List[float]:
    if not freq_arg.strip():
        return TRAINING_FREQUENCIES.copy()
    return [float(value.strip()) for value in freq_arg.split(",") if value.strip()]


def apply_random_labels(
    df: pd.DataFrame,
    frequencies: List[float],
    segment_seconds: float,
    sample_rate: int,
    seed: int,
) -> pd.DataFrame:
    """
    Place a random training frequency on the first row of each fixed-length segment.
    Mimics offline collection trial boundaries for train.py split_trials().
    """
    out = df.copy()
    rng = random.Random(seed)
    segment_len = max(1, int(segment_seconds * sample_rate))
    n_rows = len(out)
    labeled = 0
    freq_counts = {freq: 0 for freq in frequencies}

    for start_idx in range(0, n_rows, segment_len):
        freq = rng.choice(frequencies)
        color_code = COLOR_CODES[frequencies.index(freq) % len(COLOR_CODES)]
        out.at[start_idx, "Frequency"] = freq
        out.at[start_idx, "Color Code"] = color_code
        freq_counts[freq] += 1
        labeled += 1

    print(f"  random labels: {labeled} segment(s), {segment_len} samples each")
    print(f"  frequencies used: {frequencies}")
    print(f"  per-frequency segment counts: {freq_counts}")
    print(
        "  warning: random labels are NOT ground truth — only use to test the "
        "training pipeline, not for a real BCI model"
    )
    return out


def apply_segments(df: pd.DataFrame, segments: pd.DataFrame) -> pd.DataFrame:
    """
    Place Frequency / Color Code labels on the first sample at/after each timestamp.
    Matches offline collection where only the first row of a trial block is labeled.
    """
    out = df.copy()
    ts = out["_timestamp"].values
    labeled = 0

    for _, row in segments.iterrows():
        start_ts = float(row["timestamp"])
        idx_candidates = np.where(ts >= start_ts)[0]
        if len(idx_candidates) == 0:
            print(
                f"  warning: no samples at/after timestamp {start_ts}; skipping label "
                f"frequency={row['frequency']}"
            )
            continue

        idx = int(idx_candidates[0])
        out.at[idx, "Frequency"] = float(row["frequency"])
        if "color_code" in row and pd.notna(row["color_code"]):
            out.at[idx, "Color Code"] = row["color_code"]
        labeled += 1

    print(f"  applied {labeled}/{len(segments)} segment label(s)")
    if labeled == 0:
        print(
            "  warning: no labels applied — output will NOT work with train.py "
            "until segments timestamps align with the EEG file"
        )
    return out


def finalize_output(df: pd.DataFrame) -> pd.DataFrame:
    return df[OFFLINE_COLUMNS].copy()


def convert_file(
    input_path: str,
    output_path: str,
    segments_path: Optional[str],
    num_channels: int,
    random_labels: bool = False,
    segment_seconds: float = 5.0,
    sample_rate: int = 250,
    seed: int = 42,
    frequencies: Optional[List[float]] = None,
) -> None:
    print(f"converting {input_path}")
    df = load_online_csv(input_path, num_channels=num_channels)

    if segments_path:
        segments = load_segments(segments_path)
        df = apply_segments(df, segments)
    elif random_labels:
        freq_list = frequencies or TRAINING_FREQUENCIES
        df = apply_random_labels(
            df,
            frequencies=freq_list,
            segment_seconds=segment_seconds,
            sample_rate=sample_rate,
            seed=seed,
        )
    else:
        print(
            "  warning: no --segments or --random-labels — Frequency column left empty"
        )

    out = finalize_output(df)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"  wrote {output_path} ({len(out)} rows)")


def collect_input_files(input_path: str) -> List[str]:
    if os.path.isdir(input_path):
        files = sorted(glob.glob(os.path.join(input_path, "*.csv")))
        if not files:
            raise FileNotFoundError(f"No CSV files found in {input_path}")
        return files
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")
    return [input_path]


def main():
    args = parse_args()
    input_files = collect_input_files(args.input)
    frequencies = parse_frequency_list(args.frequencies)

    if args.random_labels and args.segments:
        print("error: use either --random-labels or --segments, not both", file=sys.stderr)
        sys.exit(1)

    convert_kwargs = dict(
        num_channels=args.num_channels,
        random_labels=args.random_labels,
        segment_seconds=args.segment_seconds,
        sample_rate=args.sample_rate,
        seed=args.seed,
        frequencies=frequencies,
    )

    if len(input_files) == 1:
        if not args.output:
            base = os.path.splitext(os.path.basename(input_files[0]))[0]
            args.output = os.path.join(
                "SSVEP-Data-Collection", "demo_data", f"converted_{base}.csv"
            )
        convert_file(
            input_files[0],
            args.output,
            args.segments,
            **convert_kwargs,
        )
        return

    # Batch directory mode
    output_dir = args.output_dir or os.path.join(
        "SSVEP-Data-Collection", "demo_data", "converted"
    )
    for input_path in input_files:
        base = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(output_dir, f"converted_{base}.csv")
        segments_path = args.segments
        if segments_path is None:
            candidate = os.path.join(
                os.path.dirname(input_path), f"segments_{base}.csv"
            )
            if os.path.isfile(candidate):
                segments_path = candidate
        convert_file(input_path, output_path, segments_path, **convert_kwargs)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
