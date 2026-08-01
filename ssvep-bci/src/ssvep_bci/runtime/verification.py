from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ssvep_bci.config.models import ExperimentConfig
from ssvep_bci.dsp.contracts import ProcessingResult, ProcessingStatus
from ssvep_bci.planning.models import SessionPlan, StepKind


@dataclass(frozen=True)
class VerificationSnapshot:
    scored_trials: int
    correct_trials: int
    accuracy: float | None
    latest_target_frequency_hz: float | None
    latest_predicted_frequency_hz: float | None
    latest_correct: bool | None
    per_class_counts: tuple[tuple[float, int, int], ...]


class VerificationTracker:
    def __init__(
        self,
        plan: SessionPlan,
        config: ExperimentConfig,
        model_path: Path,
        model_sha256: str,
        model_metadata: dict[str, Any],
    ) -> None:
        stimulus_frequencies = {
            stimulus.id: stimulus.frequency_hz for stimulus in config.stimuli
        }
        self._targets = {
            step.presentation_id: (
                step.trial_id,
                step.trial_number,
                step.stimulus_id,
                stimulus_frequencies[step.stimulus_id],
            )
            for step in plan.steps
            if step.kind == StepKind.STIMULUS
            and step.presentation_id is not None
            and step.stimulus_id is not None
        }
        self.frequencies = tuple(config.processing.candidate_frequencies_hz)
        self.model_path = model_path
        self.model_sha256 = model_sha256
        self.model_metadata = model_metadata
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def record(self, result: ProcessingResult) -> dict[str, Any]:
        target = self._targets.get(result.presentation_id)
        if target is None:
            raise ValueError(
                f"processing result references unknown presentation {result.presentation_id}"
            )
        trial_id, trial_number, stimulus_id, target_frequency = target
        processed = (
            result.status == ProcessingStatus.PROCESSED
            and result.predicted_frequency_hz is not None
        )
        entry = {
            "schema_version": 1,
            "window_id": result.window_id,
            "presentation_id": result.presentation_id,
            "trial_id": trial_id,
            "trial_number": trial_number,
            "target_stimulus_id": stimulus_id,
            "target_frequency_hz": target_frequency,
            "status": result.status.value,
            "predicted_index": result.predicted_index,
            "predicted_frequency_hz": result.predicted_frequency_hz,
            "correct": (
                bool(abs(result.predicted_frequency_hz - target_frequency) <= 1e-6)
                if processed
                else None
            ),
            "scores": list(result.scores),
            "elapsed_seconds": result.elapsed_seconds,
            "diagnostics": list(result.diagnostics),
        }
        with self._lock:
            if any(
                item["presentation_id"] == result.presentation_id
                for item in self._entries
            ):
                raise ValueError(
                    f"duplicate verification result for {result.presentation_id}"
                )
            self._entries.append(entry)
        return entry

    def snapshot(self) -> VerificationSnapshot:
        with self._lock:
            entries = [dict(entry) for entry in self._entries]
        scored = [entry for entry in entries if entry["correct"] is not None]
        correct = sum(bool(entry["correct"]) for entry in scored)
        latest = scored[-1] if scored else None
        per_class: list[tuple[float, int, int]] = []
        for frequency in self.frequencies:
            class_entries = [
                entry
                for entry in scored
                if abs(entry["target_frequency_hz"] - frequency) <= 1e-6
            ]
            per_class.append(
                (frequency, sum(bool(entry["correct"]) for entry in class_entries), len(class_entries))
            )
        return VerificationSnapshot(
            scored_trials=len(scored),
            correct_trials=correct,
            accuracy=(correct / len(scored) if scored else None),
            latest_target_frequency_hz=(latest["target_frequency_hz"] if latest else None),
            latest_predicted_frequency_hz=(
                latest["predicted_frequency_hz"] if latest else None
            ),
            latest_correct=(latest["correct"] if latest else None),
            per_class_counts=tuple(per_class),
        )

    def summary(self, terminal_status: str) -> dict[str, Any]:
        with self._lock:
            entries = [dict(entry) for entry in self._entries]
        scored = [entry for entry in entries if entry["correct"] is not None]
        confusion = [[0 for _ in self.frequencies] for _ in self.frequencies]
        frequency_indexes = {
            round(frequency, 6): index
            for index, frequency in enumerate(self.frequencies)
        }
        for entry in scored:
            expected = frequency_indexes[round(entry["target_frequency_hz"], 6)]
            predicted = frequency_indexes[round(entry["predicted_frequency_hz"], 6)]
            confusion[expected][predicted] += 1
        per_class = []
        recalls = []
        for index, frequency in enumerate(self.frequencies):
            total = sum(confusion[index])
            correct = confusion[index][index]
            accuracy = correct / total if total else None
            if accuracy is not None:
                recalls.append(accuracy)
            per_class.append({
                "frequency_hz": frequency,
                "correct_trials": correct,
                "scored_trials": total,
                "accuracy": accuracy,
            })
        elapsed = [float(entry["elapsed_seconds"]) for entry in entries]
        correct_total = sum(bool(entry["correct"]) for entry in scored)
        return {
            "schema_version": 1,
            "run_mode": "verification",
            "terminal_status": terminal_status,
            "model": {
                "path": str(self.model_path),
                "sha256": self.model_sha256,
                "metadata": self.model_metadata,
            },
            "candidate_frequencies_hz": list(self.frequencies),
            "planned_trials": len(self._targets),
            "result_count": len(entries),
            "scored_trials": len(scored),
            "unscored_trials": len(self._targets) - len(scored),
            "failed_result_count": len(entries) - len(scored),
            "missing_result_count": len(self._targets) - len(entries),
            "correct_trials": correct_total,
            "accuracy": correct_total / len(scored) if scored else None,
            "balanced_accuracy": sum(recalls) / len(recalls) if recalls else None,
            "confusion_matrix": confusion,
            "per_class": per_class,
            "processing_latency_seconds": {
                "mean": sum(elapsed) / len(elapsed) if elapsed else None,
                "minimum": min(elapsed) if elapsed else None,
                "maximum": max(elapsed) if elapsed else None,
            },
        }


def format_verification_scorecard(snapshot: VerificationSnapshot) -> str:
    accuracy = "--" if snapshot.accuracy is None else f"{snapshot.accuracy:.1%}"
    latest = "Awaiting first prediction"
    if snapshot.latest_target_frequency_hz is not None:
        marker = "correct" if snapshot.latest_correct else "wrong"
        latest = (
            f"Last {snapshot.latest_target_frequency_hz:g}→"
            f"{snapshot.latest_predicted_frequency_hz:g} Hz ({marker})"
        )
    classes = "  ".join(
        f"{frequency:g} Hz {correct}/{total}"
        for frequency, correct, total in snapshot.per_class_counts
    )
    return (
        f"Scored {snapshot.scored_trials}  Correct {snapshot.correct_trials}  "
        f"Accuracy {accuracy}  |  {latest}\n{classes}"
    )
