from __future__ import annotations

import csv
import json
from pathlib import Path

import joblib
import numpy as np
import pytest
import yaml

from ssvep_bci.config import enable_fbtdca_verification, load_experiment
from ssvep_bci.config.loader import ConfigurationError
from ssvep_bci.cli import _parser
from ssvep_bci.dsp.contracts import ProcessingResult, ProcessingStatus, StimulusWindow
from ssvep_bci.dsp.fbtdca_contract import BRAINDA_COMMIT, algorithm_metadata
from ssvep_bci.dsp.processors import FbccaProcessor
from ssvep_bci.planning import compile_session_plan
from ssvep_bci.recording import SessionRecorder, validate_session
from ssvep_bci.runtime.verification import (
    VerificationTracker,
    format_verification_scorecard,
)
from ssvep_bci.training.fbtdca import (
    DEFAULT_EXPERIMENT_ID,
    FbtdcaDataset,
    FbtdcaTrainingError,
    discover_collection_sessions,
    load_fbtdca_dataset,
    save_fbtdca_model,
    train_fbtdca,
)
from ssvep_bci.ui.subject_window import should_show_verification_scorecard


class FakeFbtdcaEstimator:
    classes_ = np.arange(4)

    def transform(self, value):
        assert value.ndim == 3
        assert value.shape[1:] == (8, 875)
        return np.asarray([[0.1, 0.2, 0.9, 0.3]])

    def predict(self, value):
        return np.argmax(self.transform(value), axis=1)


def _metadata(resolved, participant_id="P001"):
    config = resolved.config.processing
    return {
        "schema_version": 2,
        "processor": "fbtdca",
        "brainda_commit": BRAINDA_COMMIT,
        "algorithm": algorithm_metadata(),
        "participant_id": participant_id,
        "sampling_rate_hz": resolved.device.sampling_rate_hz,
        "channel_names": list(config.channels),
        "candidate_frequencies_hz": list(config.candidate_frequencies_hz),
        "phase_offsets_radians": [0.0] * 4,
        "label_to_frequency_hz": {
            str(index): frequency
            for index, frequency in enumerate(config.candidate_frequencies_hz)
        },
        "window_onset_offset_seconds": config.window.onset_offset_seconds,
        "window_length_seconds": config.window.length_seconds,
        "notch_enabled": config.notch.enabled,
        "notch_frequency_hz": config.notch.frequency_hz,
        "notch_quality_factor": config.notch.quality_factor,
    }


def _artifact(tmp_path: Path, resolved, participant_id="P001") -> Path:
    path = tmp_path / "fbtdca.joblib"
    joblib.dump(
        {"metadata": _metadata(resolved, participant_id), "estimator": FakeFbtdcaEstimator()},
        path,
    )
    return path


def test_collection_profile_is_balanced_and_exact() -> None:
    resolved = load_experiment("four-frequency-fbtdca")
    plan = compile_session_plan(resolved.config)
    stimuli = {stimulus.id: stimulus for stimulus in resolved.config.stimuli}
    targets = [
        step.stimulus_id for step in plan.steps if step.presentation_id is not None
    ]

    assert resolved.device.backend == "brainflow"
    assert plan.trial_count == 48
    assert plan.duration_seconds == 396
    assert tuple(stimulus.frequency_hz for stimulus in resolved.config.stimuli) == (
        8.0,
        9.0,
        13.0,
        14.0,
    )
    assert all(stimulus.phase_offset_radians == 0 for stimulus in stimuli.values())
    assert all(targets.count(stimulus_id) == 12 for stimulus_id in stimuli)
    for offset in range(0, len(targets), 4):
        assert set(targets[offset:offset + 4]) == set(stimuli)


def test_two_second_profile_matches_short_training_window() -> None:
    resolved = load_experiment("four-frequency-fbtdca-2s")

    assert resolved.config.processing.window.onset_offset_seconds == 0.25
    assert resolved.config.processing.window.length_seconds == 2.0
    assert resolved.config.protocol.stimulation_seconds == 4.0
    assert tuple(resolved.config.processing.candidate_frequencies_hz) == (
        8.0,
        9.0,
        13.0,
        14.0,
    )

    uncut = load_experiment("four-frequency-fbtdca-2s-uncut")
    assert uncut.config.processing.window.onset_offset_seconds == 0.0
    assert uncut.config.processing.window.length_seconds == 2.0
    assert uncut.config.protocol.stimulation_seconds == 4.0


def test_one_and_a_half_second_profile_uses_final_one_and_a_quarter_seconds() -> None:
    resolved = load_experiment("four-frequency-fbtdca-1p5s-stimulus")
    plan = compile_session_plan(resolved.config)

    assert resolved.config.protocol.stimulation_seconds == 1.5
    assert resolved.config.processing.window.onset_offset_seconds == 0.25
    assert resolved.config.processing.window.length_seconds == 1.25
    assert plan.trial_count == 48
    assert plan.duration_seconds == 276


def test_verification_override_and_fbtdca_processing(tmp_path) -> None:
    collection = load_experiment("four-frequency-fbtdca")
    artifact = _artifact(tmp_path, collection)
    effective = enable_fbtdca_verification(collection, artifact)

    assert collection.config.processing.enabled is False
    assert collection.config.processing.processor == "fbcca"
    assert effective.config.processing.enabled is True
    assert effective.config.processing.required is True
    assert effective.config.processing.processor == "fbtdca"

    processor = FbccaProcessor(effective, participant_id="P001")
    sample_count = 875
    eeg = np.zeros((sample_count, 8), dtype=np.float64)
    window = StimulusWindow(
        window_id="window",
        presentation_id="presentation-1",
        trial_id="trial-1",
        stimulus_id="freq-13",
        target_frequency_hz=13.0,
        candidate_frequencies_hz=effective.config.processing.candidate_frequencies_hz,
        onset_monotonic_timestamp=1.0,
        offset_monotonic_timestamp=5.0,
        analysis_start_monotonic_timestamp=1.25,
        analysis_end_monotonic_timestamp=4.75,
        sampling_rate_hz=250,
        channel_names=effective.config.processing.channels,
        eeg=eeg,
        aligned_monotonic_timestamps=1.25 + np.arange(sample_count) / 250,
        expected_sample_count=sample_count,
    )
    result = processor.process(window)

    assert result.status == ProcessingStatus.PROCESSED
    assert result.scores == (0.1, 0.2, 0.9, 0.3)
    assert result.predicted_index == 2
    assert result.predicted_frequency_hz == 13.0

    with pytest.raises(ValueError, match="participant_id"):
        FbccaProcessor(effective, participant_id="OTHER")


def test_verification_tracker_builds_scorecard_and_summary(tmp_path) -> None:
    collection = load_experiment("four-frequency-fbtdca")
    artifact = _artifact(tmp_path, collection)
    effective = enable_fbtdca_verification(collection, artifact)
    plan = compile_session_plan(effective.config)
    processor = FbccaProcessor(effective, participant_id="P001")
    tracker = VerificationTracker(
        plan,
        effective.config,
        artifact,
        processor.model_sha256 or "",
        processor.model_metadata or {},
    )
    step = next(item for item in plan.steps if item.presentation_id is not None)
    target = next(
        stimulus.frequency_hz
        for stimulus in effective.config.stimuli
        if stimulus.id == step.stimulus_id
    )
    predicted_index = effective.config.processing.candidate_frequencies_hz.index(target)
    tracker.record(ProcessingResult(
        window_id="window",
        presentation_id=step.presentation_id or "",
        status=ProcessingStatus.PROCESSED,
        processor="fbtdca",
        elapsed_seconds=0.05,
        sample_count=875,
        channel_names=effective.config.processing.channels,
        candidate_frequencies_hz=effective.config.processing.candidate_frequencies_hz,
        scores=(0.1, 0.2, 0.3, 0.4),
        predicted_index=predicted_index,
        predicted_frequency_hz=target,
    ))

    snapshot = tracker.snapshot()
    summary = tracker.summary("complete")
    scorecard = format_verification_scorecard(snapshot)
    assert snapshot.scored_trials == 1
    assert snapshot.correct_trials == 1
    assert snapshot.accuracy == 1.0
    assert "Accuracy 100.0%" in scorecard
    assert summary["accuracy"] == 1.0
    assert summary["correct_trials"] == 1
    assert sum(sum(row) for row in summary["confusion_matrix"]) == 1
    assert should_show_verification_scorecard("pre_stimulus", snapshot)
    assert should_show_verification_scorecard("inter_trial", snapshot)
    assert should_show_verification_scorecard("final_rest", snapshot)
    assert not should_show_verification_scorecard("stimulus", snapshot)


def _write_training_session(
    root: Path, name: str, participant: str, phase=0.0, repetitions: int = 1
) -> Path:
    resolved = load_experiment("four-frequency-fbtdca")
    path = root / name
    path.mkdir()
    manifest = {
        "status": "complete",
        "run_mode": "run",
        "participant_id": participant,
        "experiment_id": DEFAULT_EXPERIMENT_ID,
        "session_id": name,
        "config_hash": "same-config",
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    experiment = resolved.config.model_dump(mode="json")
    experiment["protocol"]["repetitions"] = repetitions
    for stimulus in experiment["stimuli"]:
        stimulus["phase_offset_radians"] = phase
    (path / "experiment-config.yaml").write_text(
        yaml.safe_dump(experiment, sort_keys=False), encoding="utf-8"
    )
    channels = list(resolved.config.processing.channels)
    acquisition = {
        "configured_sampling_rate_hz": 250,
        "channel_names": channels,
    }
    (path / "acquisition-metadata.json").write_text(
        json.dumps(acquisition), encoding="utf-8"
    )
    frequencies = [8.0, 9.0, 13.0, 14.0]
    stimulus_ids = ["freq-8", "freq-9", "freq-13", "freq-14"]
    events = []
    sequence = stimulus_ids * repetitions
    for index, stimulus_id in enumerate(sequence, 1):
        onset = 0.5 + (index - 1) * 4.5
        common = {
            "presentation_id": f"presentation-{index:04d}",
            "trial_id": f"trial-{index:04d}",
            "trial_number": index,
            "stimulus_id": stimulus_id,
            "payload": {"confirmed_by_frame_swap": True},
        }
        events.append({**common, "event_type": "stimulus_onset", "monotonic_timestamp": onset})
        events.append({**common, "event_type": "stimulus_offset", "monotonic_timestamp": onset + 4})
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    timestamps = np.arange(0, 1.0 + len(sequence) * 4.5, 1 / 250)
    eeg = np.zeros((len(timestamps), 8))
    generator = np.random.default_rng(sum(ord(character) for character in name))
    for index, stimulus_id in enumerate(sequence):
        frequency = frequencies[stimulus_ids.index(stimulus_id)]
        onset = 0.5 + index * 4.5
        mask = (timestamps >= onset) & (timestamps < onset + 4)
        for channel in range(8):
            eeg[mask, channel] = (
                (1 + channel * 0.05)
                * np.sin(2 * np.pi * frequency * timestamps[mask] + channel * 0.03)
                + generator.normal(0, 0.15, np.count_nonzero(mask))
            )
    with (path / "eeg_raw.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["aligned_monotonic_timestamp", *channels])
        writer.writerows(np.column_stack((timestamps, eeg)))
    return path


def test_raw_sessions_are_discovered_and_extracted(tmp_path) -> None:
    first = _write_training_session(tmp_path, "session-a", "P001")
    second = _write_training_session(tmp_path, "session-b", "P001")
    _write_training_session(tmp_path, "other", "OTHER")
    verification = _write_training_session(tmp_path, "verification", "P001")
    verification_manifest = json.loads(
        (verification / "manifest.json").read_text(encoding="utf-8")
    )
    verification_manifest["run_mode"] = "verification"
    (verification / "manifest.json").write_text(
        json.dumps(verification_manifest), encoding="utf-8"
    )

    discovered = discover_collection_sessions(tmp_path, "P001")
    dataset = load_fbtdca_dataset(discovered, "P001")

    assert discovered == (first.resolve(), second.resolve())
    assert dataset.eeg.shape == (8, 8, 875)
    assert dataset.labels.tolist() == [0, 1, 2, 3] * 2
    assert dataset.session_groups.tolist() == [0] * 4 + [1] * 4
    assert dataset.excluded_trials == ()

    short_dataset = load_fbtdca_dataset(
        discovered,
        "P001",
        window_onset_offset_seconds=0.0,
        window_length_seconds=2.0,
    )
    assert short_dataset.eeg.shape == (8, 8, 500)
    assert short_dataset.window_onset_offset_seconds == 0.0
    assert short_dataset.window_length_seconds == 2.0

    with pytest.raises(FbtdcaTrainingError, match="cannot exceed"):
        load_fbtdca_dataset(discovered, "P001", window_length_seconds=4.0)

    with pytest.raises(FbtdcaTrainingError, match="non-negative"):
        load_fbtdca_dataset(
            discovered, "P001", window_onset_offset_seconds=-0.1
        )

    with pytest.raises(FbtdcaTrainingError, match="zero phase"):
        load_fbtdca_dataset(
            [first, _write_training_session(tmp_path, "phase", "P001", phase=0.5)],
            "P001",
        )


def test_verification_override_requires_existing_model(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        enable_fbtdca_verification(
            load_experiment("four-frequency-fbtdca"), tmp_path / "missing.joblib"
        )


def test_training_rejects_nonfinite_and_incomplete_recordings(tmp_path) -> None:
    nonfinite_root = tmp_path / "nonfinite"
    nonfinite_root.mkdir()
    first = _write_training_session(nonfinite_root, "a", "P001")
    second = _write_training_session(nonfinite_root, "b", "P001")
    csv_path = second / "eeg_raw.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    fields = lines[10].split(",")
    fields[1] = "nan"
    lines[10] = ",".join(fields)
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(FbtdcaTrainingError, match="invalid numeric EEG"):
        load_fbtdca_dataset([first, second], "P001")

    incomplete_root = tmp_path / "incomplete"
    incomplete_root.mkdir()
    first = _write_training_session(incomplete_root, "a", "P001")
    second = _write_training_session(incomplete_root, "b", "P001")
    csv_path = second / "eeg_raw.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    csv_path.write_text("\n".join(lines[:-500]) + "\n", encoding="utf-8")
    with pytest.raises(FbtdcaTrainingError, match="do not cover all classes"):
        load_fbtdca_dataset([first, second], "P001")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FbtdcaTrainingError, match="at least two"):
        discover_collection_sessions(empty, "P001")


def test_verify_cli_requires_explicit_config_model_and_participant() -> None:
    args = _parser().parse_args([
        "verify",
        "--config",
        "four-frequency-fbtdca",
        "--model",
        "model.joblib",
        "--participant",
        "P001",
        "--windowed",
    ])
    assert args.command == "verify"
    assert args.config == "four-frequency-fbtdca"
    assert args.model == "model.joblib"
    assert args.participant == "P001"
    assert args.windowed


def test_verification_artifacts_are_manifested_and_checksummed(tmp_path) -> None:
    collection = load_experiment("four-frequency-fbtdca")
    artifact = _artifact(tmp_path, collection)
    effective = enable_fbtdca_verification(collection, artifact)
    effective = effective.__class__(
        config=effective.config,
        config_path=effective.config_path,
        device=effective.device,
        device_path=effective.device_path,
        output_root=tmp_path / "sessions",
        assets=effective.assets,
        classifier_path=effective.classifier_path,
    )
    recorder = SessionRecorder(
        effective,
        compile_session_plan(effective.config),
        "P001",
        run_mode="verification",
    )
    recorder.record_verification({
        "schema_version": 1,
        "presentation_id": "presentation-0001",
        "correct": True,
    })
    recorder.finalize(
        "complete",
        verification_summary={"schema_version": 1, "terminal_status": "complete"},
    )

    manifest = json.loads((recorder.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_mode"] == "verification"
    assert "verification-results.jsonl" in manifest["artifacts"]
    assert "verification-summary.json" in manifest["artifacts"]
    assert validate_session(recorder.path)["status"] == "complete"


def test_real_fbtdca_training_and_artifact_round_trip(tmp_path) -> None:
    pytest.importorskip("brainda")
    frequencies = (8.0, 9.0, 13.0, 14.0)
    samples = 875
    time = np.arange(samples) / 250
    trials = []
    labels = []
    groups = []
    generator = np.random.default_rng(42)
    for group in range(2):
        for repetition in range(3):
            for label, frequency in enumerate(frequencies):
                channels = []
                for channel in range(8):
                    phase = channel * 0.03 + repetition * 0.01
                    response = (1 + channel * 0.05) * np.sin(
                        2 * np.pi * frequency * time + phase
                    )
                    channels.append(response + generator.normal(0, 0.15, samples))
                trials.append(np.stack(channels))
                labels.append(label)
                groups.append(group)
    dataset = FbtdcaDataset(
        eeg=np.stack(trials),
        labels=np.asarray(labels),
        session_groups=np.asarray(groups),
        session_paths=(tmp_path / "a", tmp_path / "b"),
        session_ids=("a", "b"),
        config_hashes=("hash", "hash"),
        participant_id="P001",
        sampling_rate_hz=250.0,
        channel_names=("O1", "O2", "Oz", "PO3", "PO4", "P3", "P4", "Pz"),
        candidate_frequencies_hz=frequencies,
        phase_offsets_radians=(0.0, 0.0, 0.0, 0.0),
        window_onset_offset_seconds=0.25,
        window_length_seconds=3.5,
        notch_enabled=True,
        notch_frequency_hz=60.0,
        notch_quality_factor=30.0,
        excluded_trials=(),
    )

    estimator, validation = train_fbtdca(dataset)
    model_path, summary_path = save_fbtdca_model(
        dataset, estimator, validation, tmp_path / "model.joblib"
    )

    state = joblib.load(model_path)
    effective = enable_fbtdca_verification(
        load_experiment("four-frequency-fbtdca"), model_path
    )
    loaded_processor = FbccaProcessor(effective, participant_id="P001")
    assert validation["balanced_accuracy"] == 1.0
    assert state["metadata"]["schema_version"] == 2
    assert state["metadata"]["brainda_commit"]
    assert loaded_processor.model_metadata == state["metadata"]
    assert summary_path.is_file()
