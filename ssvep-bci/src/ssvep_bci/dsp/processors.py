from __future__ import annotations

import hashlib
import time

import joblib
import numpy as np
from scipy.stats import pearsonr
from sklearn.cross_decomposition import CCA

from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.dsp.contracts import ProcessingResult, ProcessingStatus, StimulusWindow
from ssvep_bci.dsp.fbtdca_contract import (
    BRAINDA_COMMIT,
    algorithm_metadata,
)
from ssvep_bci.dsp.filters import bandpass, legacy_filter_bank, notch_filter


class FilterProcessingError(RuntimeError):
    pass


def reference_templates(
    frequencies: tuple[float, ...], harmonics: int, sample_count: int, rate: float
) -> np.ndarray:
    t = np.arange(sample_count, dtype=np.float64) / rate
    output = np.empty((len(frequencies), sample_count, 2 * harmonics), dtype=np.float64)
    for frequency_index, frequency in enumerate(frequencies):
        columns: list[np.ndarray] = []
        for harmonic in range(1, harmonics + 1):
            columns.extend((
                np.sin(2 * np.pi * harmonic * frequency * t),
                np.cos(2 * np.pi * harmonic * frequency * t),
            ))
        output[frequency_index] = np.column_stack(columns)
    return output


class FbccaProcessor:
    def __init__(
        self, resolved: ResolvedExperiment, participant_id: str | None = None
    ) -> None:
        self.resolved = resolved
        self.config = resolved.config.processing
        self.participant_id = participant_id
        self.classifier = None
        self.model_frequencies: tuple[float, ...] | None = None
        self.model_metadata: dict | None = None
        self.model_sha256: str | None = None
        if self.config.processor in {"fbcca_knn", "cca_knn", "fbtdca"}:
            self._load_classifier()

    def process(self, window: StimulusWindow) -> ProcessingResult:
        started = time.perf_counter()
        if window.eeg.shape[0] != window.expected_sample_count:
            return self._result(
                window, ProcessingStatus.INSUFFICIENT_DATA, started,
                diagnostics=window.diagnostics,
            )
        if not np.all(np.isfinite(window.eeg)):
            return self._result(
                window, ProcessingStatus.INVALID_INPUT, started,
                diagnostics=window.diagnostics + ("EEG contains non-finite values",),
            )
        try:
            eeg = window.eeg
            if self.config.notch.enabled:
                try:
                    eeg = notch_filter(
                        eeg,
                        window.sampling_rate_hz,
                        self.config.notch.frequency_hz,
                        self.config.notch.quality_factor,
                    )
                except Exception as exc:
                    raise FilterProcessingError(f"notch filter failed: {exc}") from exc
            if self.config.processor == "fbtdca":
                return self._process_fbtdca(window, eeg, started)
            templates = reference_templates(
                window.candidate_frequencies_hz,
                self.config.cca.harmonics,
                window.expected_sample_count,
                window.sampling_rate_hz,
            )
            if self.config.processor == "cca_knn":
                correlations = self._cca_correlations(
                    eeg, templates, window.sampling_rate_hz
                )
                scores = correlations[0]
            else:
                correlations = self._correlations(eeg, templates, window.sampling_rate_hz)
                weights = np.arange(1, correlations.shape[0] + 1) ** -1.25 + 0.25
                scores = weights @ correlations
            if not np.all(np.isfinite(scores)):
                raise ValueError("CCA produced non-finite scores")
            if self.classifier is None:
                predicted = int(np.argmax(scores))
            else:
                predicted = int(self.classifier.predict(scores.reshape(1, -1))[0])
                if predicted < 0 or predicted >= len(window.candidate_frequencies_hz):
                    raise ValueError(
                        f"classifier returned out-of-range class index {predicted}"
                    )
            return ProcessingResult(
                window_id=window.window_id,
                presentation_id=window.presentation_id,
                status=ProcessingStatus.PROCESSED,
                processor=self.config.processor,
                elapsed_seconds=time.perf_counter() - started,
                sample_count=window.eeg.shape[0],
                channel_names=window.channel_names,
                candidate_frequencies_hz=window.candidate_frequencies_hz,
                correlations=tuple(tuple(float(v) for v in row) for row in correlations),
                scores=tuple(float(v) for v in scores),
                predicted_index=predicted,
                predicted_frequency_hz=window.candidate_frequencies_hz[predicted],
                diagnostics=window.diagnostics,
            )
        except FilterProcessingError as exc:
            return self._result(
                window, ProcessingStatus.FILTER_ERROR, started,
                diagnostics=window.diagnostics + (str(exc),),
            )
        except ValueError as exc:
            return self._result(
                window, ProcessingStatus.INVALID_INPUT, started,
                diagnostics=window.diagnostics + (str(exc),),
            )
        except Exception as exc:
            return self._result(
                window, ProcessingStatus.MODEL_ERROR, started,
                diagnostics=window.diagnostics + (str(exc),),
            )

    def _process_fbtdca(
        self, window: StimulusWindow, eeg: np.ndarray, started: float
    ) -> ProcessingResult:
        if self.classifier is None:
            raise ValueError("FBTDCA estimator is not loaded")
        model_input = np.array(eeg.T[np.newaxis, :, :], dtype=np.float64, copy=True)
        transformed = np.asarray(self.classifier.transform(model_input), dtype=np.float64)
        expected_shape = (1, len(window.candidate_frequencies_hz))
        if transformed.shape != expected_shape:
            raise ValueError(
                f"FBTDCA returned score shape {transformed.shape}, expected {expected_shape}"
            )
        scores = transformed[0]
        if not np.all(np.isfinite(scores)):
            raise ValueError("FBTDCA produced non-finite scores")
        predicted = int(np.argmax(scores))
        return ProcessingResult(
            window_id=window.window_id,
            presentation_id=window.presentation_id,
            status=ProcessingStatus.PROCESSED,
            processor=self.config.processor,
            elapsed_seconds=time.perf_counter() - started,
            sample_count=window.eeg.shape[0],
            channel_names=window.channel_names,
            candidate_frequencies_hz=window.candidate_frequencies_hz,
            scores=tuple(float(value) for value in scores),
            predicted_index=predicted,
            predicted_frequency_hz=window.candidate_frequencies_hz[predicted],
            diagnostics=window.diagnostics,
        )

    def _correlations(
        self, eeg: np.ndarray, templates: np.ndarray, sampling_rate_hz: float
    ) -> np.ndarray:
        subbands = self.config.filter_bank.subbands
        correlations = np.empty((subbands, templates.shape[0]), dtype=np.float64)
        for subband in range(subbands):
            try:
                filtered = legacy_filter_bank(eeg, sampling_rate_hz, subband)
            except Exception as exc:
                raise FilterProcessingError(
                    f"filter-bank subband {subband + 1} failed: {exc}"
                ) from exc
            for frequency_index, reference in enumerate(templates):
                cca = CCA(n_components=self.config.cca.components)
                eeg_c, ref_c = cca.fit_transform(filtered, reference)
                correlation, _ = pearsonr(eeg_c[:, 0], ref_c[:, 0])
                correlations[subband, frequency_index] = correlation
        return correlations

    def _cca_correlations(
        self, eeg: np.ndarray, templates: np.ndarray, sampling_rate_hz: float
    ) -> np.ndarray:
        try:
            filtered = bandpass(eeg, sampling_rate_hz, 7.0, 16.0)
        except Exception as exc:
            raise FilterProcessingError(f"CCA band-pass filter failed: {exc}") from exc
        correlations = np.empty((1, templates.shape[0]), dtype=np.float64)
        for frequency_index, reference in enumerate(templates):
            cca = CCA(n_components=self.config.cca.components)
            eeg_c, ref_c = cca.fit_transform(filtered, reference)
            correlation, _ = pearsonr(eeg_c[:, 0], ref_c[:, 0])
            correlations[0, frequency_index] = correlation
        return correlations

    def _load_classifier(self) -> None:
        path = self.resolved.classifier_path
        if path is None:
            raise ValueError("classifier processor requires a resolved model path")
        try:
            state = joblib.load(path)
        except Exception as exc:
            raise ValueError(f"cannot load classifier artifact {path}: {exc}") from exc
        if not isinstance(state, dict):
            raise ValueError("classifier artifact must contain a mapping")
        metadata = state.get("metadata")
        if self.config.processor == "fbtdca":
            self._load_fbtdca_classifier(state, metadata, path)
            return
        if metadata is None:
            if not self.config.classifier.allow_unsafe_legacy_joblib:
                raise ValueError(
                    "legacy classifier has no compatibility metadata; set "
                    "allow_unsafe_legacy_joblib explicitly to load it"
                )
            estimator = state.get("knn")
            frequencies = state.get("frequencies")
        else:
            if metadata.get("schema_version") != 1:
                raise ValueError("unsupported classifier metadata schema")
            expected = {
                "processor": self.config.processor,
                "sampling_rate_hz": self.resolved.device.sampling_rate_hz,
                "window_length_seconds": self.config.window.length_seconds,
                "channel_names": list(self.config.channels),
                "candidate_frequencies_hz": list(self.config.candidate_frequencies_hz),
                "harmonics": self.config.cca.harmonics,
                "components": self.config.cca.components,
            }
            mismatches = [key for key, value in expected.items() if metadata.get(key) != value]
            if mismatches:
                raise ValueError(
                    "classifier metadata is incompatible: " + ", ".join(mismatches)
                )
            estimator = state.get("estimator")
            frequencies = metadata.get("candidate_frequencies_hz")
        if estimator is None or not hasattr(estimator, "predict"):
            raise ValueError("classifier artifact has no estimator with predict()")
        normalized = tuple(float(value) for value in frequencies)
        if normalized != tuple(self.config.candidate_frequencies_hz):
            raise ValueError("classifier frequency order does not match configuration")
        self.classifier = estimator
        self.model_frequencies = normalized

    def _load_fbtdca_classifier(self, state: dict, metadata: object, path) -> None:
        if not isinstance(metadata, dict) or metadata.get("schema_version") != 2:
            raise ValueError("FBTDCA classifier requires metadata schema version 2")
        if metadata.get("processor") != "fbtdca":
            raise ValueError("classifier metadata processor is not fbtdca")
        if metadata.get("brainda_commit") != BRAINDA_COMMIT:
            raise ValueError("FBTDCA classifier uses an unsupported Brainda revision")
        if metadata.get("algorithm") != algorithm_metadata():
            raise ValueError("FBTDCA classifier algorithm settings are incompatible")
        if self.participant_id is None:
            raise ValueError("FBTDCA verification requires a participant ID")
        expected = {
            "participant_id": self.participant_id,
            "sampling_rate_hz": self.resolved.device.sampling_rate_hz,
            "channel_names": list(self.config.channels),
            "candidate_frequencies_hz": list(self.config.candidate_frequencies_hz),
            "phase_offsets_radians": [0.0] * len(self.config.candidate_frequencies_hz),
            "window_onset_offset_seconds": self.config.window.onset_offset_seconds,
            "window_length_seconds": self.config.window.length_seconds,
            "notch_enabled": self.config.notch.enabled,
            "notch_frequency_hz": self.config.notch.frequency_hz,
            "notch_quality_factor": self.config.notch.quality_factor,
        }
        mismatches = [key for key, value in expected.items() if metadata.get(key) != value]
        if mismatches:
            raise ValueError(
                "FBTDCA classifier metadata is incompatible: " + ", ".join(mismatches)
            )
        estimator = state.get("estimator")
        if estimator is None or not all(
            hasattr(estimator, method) for method in ("transform", "predict")
        ):
            raise ValueError("FBTDCA artifact has no estimator with transform()/predict()")
        classes = tuple(int(value) for value in getattr(estimator, "classes_", ()))
        expected_classes = tuple(range(len(self.config.candidate_frequencies_hz)))
        if classes != expected_classes:
            raise ValueError(
                f"FBTDCA estimator classes {classes} do not match {expected_classes}"
            )
        labels = metadata.get("label_to_frequency_hz")
        expected_labels = {
            str(index): frequency
            for index, frequency in enumerate(self.config.candidate_frequencies_hz)
        }
        if labels != expected_labels:
            raise ValueError("FBTDCA label-to-frequency mapping is incompatible")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self.classifier = estimator
        self.model_frequencies = tuple(self.config.candidate_frequencies_hz)
        self.model_metadata = metadata
        self.model_sha256 = digest.hexdigest()

    def _result(
        self,
        window: StimulusWindow,
        status: ProcessingStatus,
        started: float,
        diagnostics: tuple[str, ...],
    ) -> ProcessingResult:
        return ProcessingResult(
            window_id=window.window_id,
            presentation_id=window.presentation_id,
            status=status,
            processor=self.config.processor,
            elapsed_seconds=time.perf_counter() - started,
            sample_count=window.eeg.shape[0],
            channel_names=window.channel_names,
            candidate_frequencies_hz=window.candidate_frequencies_hz,
            diagnostics=diagnostics,
        )
