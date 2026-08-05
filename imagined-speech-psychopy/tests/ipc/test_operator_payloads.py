from pydantic import ValidationError
import pytest

from imagined_speech.config import SubjectWindowMode
from imagined_speech.ipc.messages import (
    AcquisitionStatePayload,
    CreateSessionPayload,
    ServiceStatePayload,
    SubjectDisplayTargetPayload,
)


def test_create_session_payload_carries_all_setup_overrides() -> None:
    value = CreateSessionPayload(
        config_path="experiment.yaml",
        participant_id="P001",
        session_label="RUN001",
        output_root="sessions",
        device_profile_path="device.yaml",
        random_seed=42,
        screen_index=1,
        subject_display=SubjectDisplayTargetPayload(
            device_name=r"\\.\DISPLAY1",
            psychopy_index=0,
            qt_index=1,
            qt_name=r"\\.\DISPLAY1",
            geometry=(-1920, 1045, 1920, 1080),
            primary=False,
        ),
        window_mode=SubjectWindowMode.CENTER,
    )

    assert value.device_profile_path == "device.yaml"
    assert value.random_seed == 42
    assert value.screen_index == 1
    assert value.subject_display is not None
    assert value.subject_display.device_name == r"\\.\DISPLAY1"
    assert value.window_mode == SubjectWindowMode.CENTER


def test_operator_status_payloads_are_strict_and_validate_statistics() -> None:
    state = ServiceStatePayload(
        subject_connected=True,
        active_session_id=None,
        active_runtime_state=None,
        can_create_session=True,
    )
    assert state.can_create_session

    with pytest.raises(ValidationError):
        ServiceStatePayload.model_validate({
            **state.model_dump(),
            "unexpected": True,
        })
    with pytest.raises(ValidationError):
        AcquisitionStatePayload(
            running=True,
            sample_count=-1,
            dropped_batches=0,
            dropped_samples=0,
            timestamp_discontinuities=0,
            read_errors=0,
            write_errors=0,
            last_health_kind="running",
            last_health_severity="info",
            channel_names=("F3",),
            recent_samples=(),
            sampling_rate_hz=250,
            eeg_channel_indexes=(0,),
            eeg_channel_labels=("F3",),
            raw_file_size_bytes=0,
            free_storage_bytes=1,
        )
