from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import yaml
from scipy import signal
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix

from ssvep_bci.dsp.fbtdca_contract import (
    BRAINDA_COMMIT,
    N_BANDS,
    N_COMPONENTS,
    N_HARMONICS,
    PADDING_LEN,
    algorithm_metadata,
)
from ssvep_bci.dsp.windows import EDGE_TOLERANCE_SECONDS


DEFAULT_EXPERIMENT_ID = "four-frequency-fbtdca-collection"
TIMESTAMP_COLUMN = "aligned_monotonic_timestamp"


class FbtdcaTrainingError(RuntimeError):
    pass


@dataclass(frozen=True)
class FbtdcaDataset:
    eeg: np.ndarray
    labels: np.ndarray
    session_groups: np.ndarray
    session_paths: tuple[Path, ...]
    session_ids: tuple[str, ...]
    config_hashes: tuple[str, ...]
    participant_id: str
    sampling_rate_hz: float
    channel_names: tuple[str, ...]
    candidate_frequencies_hz: tuple[float, ...]
    phase_offsets_radians: tuple[float, ...]
    window_onset_offset_seconds: float
    window_length_seconds: float
    notch_enabled: bool
    notch_frequency_hz: float
    notch_quality_factor: float
    excluded_trials: tuple[dict[str, Any], ...]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FbtdcaTrainingError(f"{path} must contain a JSON object")
    return value


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FbtdcaTrainingError(f"{path} must contain a YAML mapping")
    return value


def discover_collection_sessions(
    session_root: str | Path,
    participant_id: str,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
) -> tuple[Path, ...]:
    root = Path(session_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"session root does not exist: {root}")
    sessions: list[Path] = []
    required = {
        "manifest.json",
        "experiment-config.yaml",
        "acquisition-metadata.json",
        "events.jsonl",
        "eeg_raw.csv",
    }
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not all((path / name).is_file() for name in required):
            continue
        manifest = _json(path / "manifest.json")
        if (
            manifest.get("status") == "complete"
            and manifest.get("participant_id") == participant_id
            and manifest.get("experiment_id") == experiment_id
            and manifest.get("run_mode", "run") != "verification"
        ):
            sessions.append(path.resolve())
    if len(sessions) < 2:
        raise FbtdcaTrainingError(
            "at least two complete, non-verification collection sessions are required"
        )
    return tuple(sessions)


def _read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FbtdcaTrainingError(
                f"{path}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        events.append(value)
    return events


def _pair_events(
    events: Iterable[dict[str, Any]], frequencies: dict[str, float]
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    for event in events:
        kind = event.get("event_type")
        if kind not in {"stimulus_onset", "stimulus_offset"}:
            continue
        presentation_id = event.get("presentation_id")
        if not presentation_id:
            raise FbtdcaTrainingError(f"{kind} event has no presentation_id")
        grouped = buckets.setdefault(str(presentation_id), {})
        if kind in grouped:
            raise FbtdcaTrainingError(f"duplicate {kind} for {presentation_id}")
        grouped[kind] = event
    pairs = []
    for presentation_id, grouped in buckets.items():
        if set(grouped) != {"stimulus_onset", "stimulus_offset"}:
            raise FbtdcaTrainingError(f"unpaired stimulus events for {presentation_id}")
        onset = grouped["stimulus_onset"]
        offset = grouped["stimulus_offset"]
        if not onset.get("payload", {}).get("confirmed_by_frame_swap"):
            raise FbtdcaTrainingError(f"unconfirmed onset for {presentation_id}")
        if not offset.get("payload", {}).get("confirmed_by_frame_swap"):
            raise FbtdcaTrainingError(f"unconfirmed offset for {presentation_id}")
        stimulus_id = str(onset.get("stimulus_id"))
        if stimulus_id != str(offset.get("stimulus_id")) or stimulus_id not in frequencies:
            raise FbtdcaTrainingError(f"invalid stimulus identity for {presentation_id}")
        onset_time = float(onset["monotonic_timestamp"])
        offset_time = float(offset["monotonic_timestamp"])
        if offset_time <= onset_time:
            raise FbtdcaTrainingError(f"invalid event order for {presentation_id}")
        pairs.append({
            "presentation_id": presentation_id,
            "trial_id": onset.get("trial_id"),
            "trial_number": onset.get("trial_number"),
            "stimulus_id": stimulus_id,
            "frequency_hz": frequencies[stimulus_id],
            "onset": onset_time,
            "offset": offset_time,
        })
    return sorted(pairs, key=lambda item: item["onset"])


def _load_numeric_csv(path: Path, channel_names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle), [])
    columns = (TIMESTAMP_COLUMN, *channel_names)
    missing = [name for name in columns if name not in header]
    if missing:
        raise FbtdcaTrainingError(f"{path} is missing columns: {missing}")
    indexes = tuple(header.index(name) for name in columns)
    values = np.loadtxt(path, delimiter=",", skiprows=1, usecols=indexes, ndmin=2)
    if values.shape[1] != len(columns) or not np.all(np.isfinite(values)):
        raise FbtdcaTrainingError(f"{path} contains invalid numeric EEG data")
    timestamps = values[:, 0]
    if timestamps.size < 2 or np.any(np.diff(timestamps) <= 0):
        raise FbtdcaTrainingError(f"{path} timestamps must be strictly increasing")
    return timestamps, values[:, 1:]


def _extract_epoch(
    timestamps: np.ndarray,
    eeg: np.ndarray,
    start: float,
    length_seconds: float,
    expected_samples: int,
) -> np.ndarray | None:
    end = start + length_seconds
    candidates = np.flatnonzero(
        (timestamps >= start - EDGE_TOLERANCE_SECONDS)
        & (timestamps < end + EDGE_TOLERANCE_SECONDS)
    )
    if len(candidates) < expected_samples:
        return None
    best_offset = min(
        range(len(candidates) - expected_samples + 1),
        key=lambda offset: abs(
            (
                timestamps[candidates[offset]]
                + timestamps[candidates[offset + expected_samples - 1]]
            )
            / 2
            - (start + end) / 2
        ),
    )
    selected = candidates[best_offset:best_offset + expected_samples]
    return np.array(eeg[selected].T, dtype=np.float64, copy=True)


def load_fbtdca_dataset(
    session_paths: Iterable[str | Path],
    participant_id: str,
    *,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    window_onset_offset_seconds: float | None = None,
    window_length_seconds: float | None = None,
) -> FbtdcaDataset:
    requested_onset_offset = (
        None
        if window_onset_offset_seconds is None
        else float(window_onset_offset_seconds)
    )
    if requested_onset_offset is not None and (
        not np.isfinite(requested_onset_offset) or requested_onset_offset < 0
    ):
        raise FbtdcaTrainingError(
            "window onset offset override must be finite and non-negative"
        )
    requested_window_length = (
        None if window_length_seconds is None else float(window_length_seconds)
    )
    if requested_window_length is not None and (
        not np.isfinite(requested_window_length) or requested_window_length <= 0
    ):
        raise FbtdcaTrainingError("window length override must be finite and positive")
    paths = tuple(Path(path).expanduser().resolve() for path in session_paths)
    if len(paths) < 2:
        raise FbtdcaTrainingError("leave-one-session-out training requires two sessions")
    trials: list[np.ndarray] = []
    labels: list[int] = []
    groups: list[int] = []
    session_ids: list[str] = []
    config_hashes: list[str] = []
    excluded: list[dict[str, Any]] = []
    compatibility: dict[str, Any] | None = None

    for group, path in enumerate(paths):
        manifest = _json(path / "manifest.json")
        experiment = _yaml(path / "experiment-config.yaml")
        acquisition = _json(path / "acquisition-metadata.json")
        if manifest.get("status") != "complete":
            raise FbtdcaTrainingError(f"session is not complete: {path}")
        if manifest.get("run_mode", "run") == "verification":
            raise FbtdcaTrainingError(f"verification session cannot train a model: {path}")
        if manifest.get("participant_id") != participant_id:
            raise FbtdcaTrainingError(f"participant mismatch in {path}")
        if manifest.get("experiment_id") != experiment_id:
            raise FbtdcaTrainingError(f"experiment mismatch in {path}")
        if config_hashes and manifest.get("config_hash") != config_hashes[0]:
            raise FbtdcaTrainingError(f"config hash mismatch in {path}")

        processing = experiment["processing"]
        window = processing["window"]
        notch = processing["notch"]
        configured_onset_offset = float(window["onset_offset_seconds"])
        configured_window_length = float(window["length_seconds"])
        effective_onset_offset = (
            configured_onset_offset
            if requested_onset_offset is None
            else requested_onset_offset
        )
        effective_window_length = (
            configured_window_length
            if requested_window_length is None
            else requested_window_length
        )
        if effective_window_length > configured_window_length + 1e-12:
            raise FbtdcaTrainingError(
                "window length override cannot exceed the recorded processing window "
                f"({configured_window_length:g} seconds)"
            )
        stimulation_seconds = float(experiment["protocol"]["stimulation_seconds"])
        if effective_onset_offset + effective_window_length > stimulation_seconds + 1e-12:
            raise FbtdcaTrainingError(
                "requested analysis window exceeds the recorded stimulation interval "
                f"({stimulation_seconds:g} seconds)"
            )
        frequencies = tuple(float(value) for value in processing["candidate_frequencies_hz"])
        if len(frequencies) < 2:
            raise FbtdcaTrainingError("FBTDCA dataset requires at least two frequencies")
        stimuli = experiment["stimuli"]
        stimulus_frequencies = {
            str(item["id"]): float(item["frequency_hz"]) for item in stimuli
        }
        phase_by_frequency = {
            float(item["frequency_hz"]): float(item.get("phase_offset_radians", 0.0))
            for item in stimuli
        }
        phases = tuple(phase_by_frequency[frequency] for frequency in frequencies)
        if any(abs(value) > 1e-12 for value in phases):
            raise FbtdcaTrainingError("FBTDCA dataset requires zero phase offsets")
        current = {
            "sampling_rate_hz": float(acquisition["configured_sampling_rate_hz"]),
            "channel_names": tuple(acquisition["channel_names"]),
            "candidate_frequencies_hz": frequencies,
            "phase_offsets_radians": phases,
            "window_onset_offset_seconds": effective_onset_offset,
            "window_length_seconds": effective_window_length,
            "notch_enabled": bool(notch["enabled"]),
            "notch_frequency_hz": float(notch["frequency_hz"]),
            "notch_quality_factor": float(notch["quality_factor"]),
        }
        if compatibility is None:
            compatibility = current
        elif current != compatibility:
            differences = [key for key in current if current[key] != compatibility[key]]
            raise FbtdcaTrainingError(
                f"session configuration mismatch in {path}: {', '.join(differences)}"
            )
        rate = current["sampling_rate_hz"]
        expected_samples = round(rate * current["window_length_seconds"])
        timestamps, raw_eeg = _load_numeric_csv(path / "eeg_raw.csv", current["channel_names"])
        pairs = _pair_events(_read_events(path / "events.jsonl"), stimulus_frequencies)
        counts = Counter(pair["frequency_hz"] for pair in pairs)
        protocol = experiment["protocol"]
        expected_counts = Counter(
            stimulus_frequencies[stimulus_id]
            for _ in range(int(protocol["repetitions"]))
            for stimulus_id in protocol["stimulus_sequence"]
        )
        if counts != expected_counts:
            raise FbtdcaTrainingError(
                f"session event counts do not match its protocol: {path}"
            )
        for pair in pairs:
            start = pair["onset"] + current["window_onset_offset_seconds"]
            if pair["offset"] + EDGE_TOLERANCE_SECONDS < start + current["window_length_seconds"]:
                epoch = None
            else:
                epoch = _extract_epoch(
                    timestamps,
                    raw_eeg,
                    start,
                    current["window_length_seconds"],
                    expected_samples,
                )
            if epoch is None:
                excluded.append({
                    "session_id": manifest["session_id"],
                    "presentation_id": pair["presentation_id"],
                    "reason": "incomplete timestamp window",
                })
                continue
            if current["notch_enabled"]:
                b, a = signal.iirnotch(
                    current["notch_frequency_hz"],
                    current["notch_quality_factor"],
                    rate,
                )
                epoch = signal.filtfilt(b, a, epoch, axis=-1)
            trials.append(epoch)
            labels.append(frequencies.index(pair["frequency_hz"]))
            groups.append(group)
        session_ids.append(str(manifest["session_id"]))
        config_hashes.append(str(manifest["config_hash"]))

    assert compatibility is not None
    y = np.asarray(labels, dtype=np.int64)
    run_ids = np.asarray(groups, dtype=np.int64)
    expected_classes = set(range(len(compatibility["candidate_frequencies_hz"])))
    for group, path in enumerate(paths):
        if set(y[run_ids == group].tolist()) != expected_classes:
            raise FbtdcaTrainingError(
                f"complete epochs in session do not cover all classes: {path}"
            )
    return FbtdcaDataset(
        eeg=np.stack(trials),
        labels=y,
        session_groups=run_ids,
        session_paths=paths,
        session_ids=tuple(session_ids),
        config_hashes=tuple(config_hashes),
        participant_id=participant_id,
        excluded_trials=tuple(excluded),
        **compatibility,
    )


def _make_model(dataset: FbtdcaDataset):
    from brainda.algorithms.decomposition import FBTDCA, generate_filterbank

    passbands = [[8 * band, 90] for band in range(1, N_BANDS + 1)]
    stopbands = [[8 * band - 2, 95] for band in range(1, N_BANDS + 1)]
    filterbank = generate_filterbank(
        passbands, stopbands, int(dataset.sampling_rate_hz), order=4, rp=1
    )
    weights = np.arange(1, N_BANDS + 1, dtype=np.float64) ** -1.25 + 0.25
    return FBTDCA(
        filterbank,
        PADDING_LEN,
        n_components=N_COMPONENTS,
        filterweights=weights,
    )


def _references(dataset: FbtdcaDataset) -> np.ndarray:
    from brainda.algorithms.decomposition import generate_cca_references

    return generate_cca_references(
        np.asarray(dataset.candidate_frequencies_hz),
        int(dataset.sampling_rate_hz),
        dataset.window_length_seconds,
        phases=np.zeros(len(dataset.candidate_frequencies_hz)),
        n_harmonics=N_HARMONICS,
    )


def train_fbtdca(dataset: FbtdcaDataset) -> tuple[Any, dict[str, Any]]:
    if dataset.eeg.shape[1] < N_COMPONENTS:
        raise FbtdcaTrainingError(
            f"FBTDCA needs at least {N_COMPONENTS} channels for {N_COMPONENTS} components"
        )
    references = _references(dataset)
    class_labels = np.arange(len(dataset.candidate_frequencies_hz))
    expected_classes = set(class_labels.tolist())
    all_true: list[int] = []
    all_predicted: list[int] = []
    folds = []
    for group in np.unique(dataset.session_groups):
        train = dataset.session_groups != group
        test = dataset.session_groups == group
        if set(dataset.labels[train].tolist()) != expected_classes:
            raise FbtdcaTrainingError("a leave-one-session-out fold lacks a training class")
        model = _make_model(dataset).fit(
            dataset.eeg[train].copy(), dataset.labels[train], Yf=references
        )
        predicted = np.asarray(model.predict(dataset.eeg[test].copy()), dtype=np.int64)
        folds.append({
            "held_out_session_id": dataset.session_ids[int(group)],
            "trial_count": int(np.count_nonzero(test)),
            "accuracy": float(accuracy_score(dataset.labels[test], predicted)),
            "balanced_accuracy": float(
                balanced_accuracy_score(dataset.labels[test], predicted)
            ),
        })
        all_true.extend(dataset.labels[test].tolist())
        all_predicted.extend(predicted.tolist())
    matrix = confusion_matrix(all_true, all_predicted, labels=class_labels)
    normalized_matrix = matrix / matrix.sum(axis=1, keepdims=True)
    recalls = normalized_matrix.diagonal()
    report = {
        "method": "leave-one-session-out",
        "accuracy": float(accuracy_score(all_true, all_predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(all_true, all_predicted)),
        "confusion_matrix": matrix.tolist(),
        "normalized_confusion_matrix": normalized_matrix.tolist(),
        "per_class_recall": {
            str(frequency): float(recall)
            for frequency, recall in zip(dataset.candidate_frequencies_hz, recalls, strict=True)
        },
        "folds": folds,
    }
    final_model = _make_model(dataset).fit(
        dataset.eeg.copy(), dataset.labels, Yf=references
    )
    return final_model, report


def save_fbtdca_model(
    dataset: FbtdcaDataset,
    estimator: Any,
    validation: dict[str, Any],
    model_path: str | Path,
) -> tuple[Path, Path]:
    path = Path(model_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 2,
        "processor": "fbtdca",
        "participant_id": dataset.participant_id,
        "sampling_rate_hz": dataset.sampling_rate_hz,
        "channel_names": list(dataset.channel_names),
        "candidate_frequencies_hz": list(dataset.candidate_frequencies_hz),
        "phase_offsets_radians": list(dataset.phase_offsets_radians),
        "label_to_frequency_hz": {
            str(index): frequency
            for index, frequency in enumerate(dataset.candidate_frequencies_hz)
        },
        "window_onset_offset_seconds": dataset.window_onset_offset_seconds,
        "window_length_seconds": dataset.window_length_seconds,
        "notch_enabled": dataset.notch_enabled,
        "notch_frequency_hz": dataset.notch_frequency_hz,
        "notch_quality_factor": dataset.notch_quality_factor,
        "preprocessing": [
            "frame-confirmed timestamp epoch extraction",
            "configured IIR notch filter",
            "FBTDCA internal per-trial per-channel mean centering",
            "FBTDCA filter bank",
        ],
        "algorithm": algorithm_metadata(),
        "brainda_commit": BRAINDA_COMMIT,
        "versions": {
            "brainda": version("brainda"),
            "numpy": version("numpy"),
            "scipy": version("scipy"),
            "scikit-learn": version("scikit-learn"),
        },
        "source_sessions": [
            {
                "session_id": session_id,
                "config_hash": config_hash,
                "path": str(session_path),
            }
            for session_id, config_hash, session_path in zip(
                dataset.session_ids,
                dataset.config_hashes,
                dataset.session_paths,
                strict=True,
            )
        ],
    }
    joblib.dump({"metadata": metadata, "estimator": estimator}, path, compress=3)
    summary_path = path.with_name(path.stem + "_summary.json")
    summary = {
        "model_path": str(path),
        "metadata": metadata,
        "trial_count": int(len(dataset.labels)),
        "class_counts": {
            str(frequency): int(np.count_nonzero(dataset.labels == index))
            for index, frequency in enumerate(dataset.candidate_frequencies_hz)
        },
        "excluded_trials": list(dataset.excluded_trials),
        "validation": validation,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path, summary_path
