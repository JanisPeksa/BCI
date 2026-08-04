import csv
import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from imagined_speech.acquisition import AcquisitionRecorder, create_acquisition_backend
from imagined_speech.acquisition.brainflow_backend import BrainFlowAcquisitionBackend
from imagined_speech.acquisition.lsl_backend import LSLAcquisitionBackend
from imagined_speech.acquisition.lsl_publisher import publish_synthetic_lsl
from imagined_speech.acquisition.synthetic import SyntheticAcquisitionBackend
from imagined_speech.cli import default_config_path
from imagined_speech.config import load_device_profile, load_experiment
from imagined_speech.runtime.clock import VirtualClock
from imagined_speech.runtime.protocol import ProtocolEngine
from imagined_speech.events import CompositeEventSink
from imagined_speech.planning import compile_session_plan
from imagined_speech.recording import SessionWriter, validate_session
from imagined_speech.runtime.simulation import run_virtual


RESOURCE_ROOT = Path(__file__).parents[2] / "src" / "imagined_speech" / "resources"


def test_synthetic_backend_generates_deterministic_samples_and_markers() -> None:
    profile = load_device_profile(RESOURCE_ROOT / "devices" / "synthetic.yaml")
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    backend = SyntheticAcquisitionBackend(profile, clock)
    backend.prepare()
    backend.start()
    backend.insert_marker(41, 0.5)
    clock.advance(1)

    batch = backend.read_available()

    assert batch is not None
    assert batch.sample_count == 250
    assert len(batch.samples[0]) == 9
    assert batch.samples[125][-1] == 41
    assert batch.samples[0][0] != batch.samples[1][0]


def test_virtual_session_records_aligned_synthetic_eeg(tmp_path: Path) -> None:
    resolved = load_experiment(default_config_path())
    plan = compile_session_plan(resolved.config)
    clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    writer = SessionWriter(
        resolved, plan, "ACQ001", tmp_path, auto_finalize=False
    )
    backend = create_acquisition_backend(resolved, clock)
    acquisition = AcquisitionRecorder(writer.path, backend, clock)
    engine = ProtocolEngine(
        writer.session_id,
        plan,
        resolved.config,
        clock,
        CompositeEventSink(acquisition, writer),
    )

    acquisition.start()
    clock.advance(resolved.device.pre_roll_seconds)
    acquisition.capture_available()
    run_virtual(engine)
    clock.advance(resolved.device.post_roll_seconds)
    acquisition.capture_available()
    acquisition.stop()
    for artifact in acquisition.artifact_names:
        writer.register_artifact(artifact)
    writer.finalize("complete")

    report = validate_session(writer.path)
    assert report.sample_count == 4000
    assert report.event_count == 43

    metadata = json.loads(
        (writer.path / "acquisition-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "complete"
    assert metadata["dropped_samples"] == 0
    assert metadata["timestamp_discontinuities"] == 0
    assert metadata["effective_sampling_rate_hz"] == pytest.approx(250, rel=0.001)

    with (writer.path / "eeg_raw.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle)
        header = next(rows)
        marker_index = header.index("marker")
        embedded_markers = [float(row[marker_index]) for row in rows]
    assert any(marker != 0 for marker in embedded_markers)


def test_lsl_backend_reads_chunks_without_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = load_device_profile(RESOURCE_ROOT / "devices" / "lsl.yaml")

    class FakeInfo:
        def channel_count(self) -> int:
            return 8

        def name(self) -> str:
            return "imagined-speech-synthetic"

        def type(self) -> str:
            return "EEG"

        def source_id(self) -> str:
            return "fake-source"

        def uid(self) -> str:
            return "fake-uid"

        def hostname(self) -> str:
            return "localhost"

        def nominal_srate(self) -> float:
            return 250.0

    class FakeInlet:
        def __init__(self, info: FakeInfo, **kwargs: object) -> None:
            del info, kwargs

        def open_stream(self, timeout: float) -> None:
            del timeout

        def time_correction(self, timeout: float) -> float:
            del timeout
            return 0.25

        def pull_chunk(self, **kwargs: object):
            del kwargs
            return [[1.0] * 8, [2.0] * 8], [10.0, 10.004]

        def close_stream(self) -> None:
            return None

    fake_pylsl = SimpleNamespace(
        resolve_byprop=lambda *args, **kwargs: [FakeInfo()],
        StreamInlet=FakeInlet,
    )
    monkeypatch.setitem(sys.modules, "pylsl", fake_pylsl)
    backend = LSLAcquisitionBackend(profile)
    backend.prepare()
    backend.start()

    batch = backend.read_available()

    assert batch is not None
    assert batch.sample_count == 2
    assert batch.corrected_timestamps == (10.25, 10.254)
    assert backend.supports_embedded_markers is False


def test_real_lsl_publisher_and_inlet_transport_samples() -> None:
    pytest.importorskip("pylsl")
    profile = load_device_profile(RESOURCE_ROOT / "devices" / "lsl.yaml")
    publisher = threading.Thread(
        target=publish_synthetic_lsl,
        kwargs={"profile": profile, "duration_seconds": 4.0},
        daemon=True,
    )
    publisher.start()
    time.sleep(0.5)
    backend = LSLAcquisitionBackend(profile)
    backend.prepare()
    backend.start()

    received = 0
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and received < 100:
        batch = backend.read_available()
        if batch is not None:
            received += batch.sample_count
        time.sleep(0.01)
    backend.close()
    publisher.join(timeout=5)

    assert received >= 100


def test_brainflow_adapter_runs_against_synthetic_board() -> None:
    pytest.importorskip("brainflow")
    experiment = load_experiment(
        RESOURCE_ROOT / "configs" / "smoke_brainflow.yaml"
    )
    backend = create_acquisition_backend(experiment, VirtualClock())
    backend.prepare()
    backend.start()
    time.sleep(0.15)

    batch = backend.read_available()
    backend.stop()
    backend.close()

    assert batch is not None
    assert batch.sample_count > 0
    assert len(batch.samples[0]) == 32
    assert backend.metadata["marker_channel"] == 31


def test_brainflow_replay_reads_generated_recording(tmp_path: Path) -> None:
    pytest.importorskip("brainflow")
    numpy = pytest.importorskip("numpy")
    from brainflow.data_filter import DataFilter

    experiment = load_experiment(
        RESOURCE_ROOT / "configs" / "smoke_brainflow.yaml"
    )
    source = create_acquisition_backend(experiment, VirtualClock())
    source.prepare()
    source.start()
    time.sleep(0.15)
    source_batch = source.read_available()
    source.stop()
    source.close()
    assert source_batch is not None

    replay_file = tmp_path / "brainflow-replay.csv"
    DataFilter.write_file(
        numpy.ascontiguousarray(
            numpy.asarray(source_batch.samples, dtype=float).T
        ),
        str(replay_file),
        "w",
    )
    replay_profile = experiment.device.model_copy(
        update={
            "profile_id": "generated_replay",
            "backend": "replay",
            "board_id": -3,
            "connection": {"file": str(replay_file), "master_board": -1},
        }
    )
    replay = BrainFlowAcquisitionBackend(replay_profile, tmp_path / "profile.yaml")
    replay.prepare()
    replay.start()
    time.sleep(0.15)
    replay_batch = replay.read_available()
    replay.stop()
    replay.close()

    assert replay_batch is not None
    assert replay_batch.sample_count > 0
    assert len(replay_batch.samples[0]) == 32
