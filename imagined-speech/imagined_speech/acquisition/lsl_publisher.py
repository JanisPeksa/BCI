"""Standalone synthetic LSL outlet for hardware-free integration runs."""

from __future__ import annotations

import time
from pathlib import Path

from imagined_speech.acquisition.base import AcquisitionError
from imagined_speech.acquisition.synthetic import synthetic_eeg_sample
from imagined_speech.config import DeviceProfile, load_device_profile


def default_lsl_profile_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "devices"
        / "lsl.yaml"
    )


def publish_synthetic_lsl(
    profile: DeviceProfile,
    *,
    duration_seconds: float | None = None,
    stream_name: str | None = None,
) -> int:
    try:
        import pylsl
    except ImportError as exc:
        raise AcquisitionError(
            "pylsl is not installed; install the 'acquisition' extra"
        ) from exc
    if profile.backend != "lsl":
        raise ValueError("synthetic LSL publisher requires an LSL device profile")

    name = stream_name or str(
        profile.connection.get("stream_name") or "imagined-speech-synthetic"
    )
    stream_type = str(profile.connection.get("stream_type") or "EEG")
    info = pylsl.StreamInfo(
        name,
        stream_type,
        len(profile.eeg_channels),
        profile.sampling_rate_hz,
        pylsl.cf_float32,
        f"{name}-source",
    )
    channels = info.desc().append_child("channels")
    for channel in profile.eeg_channels:
        node = channels.append_child("channel")
        node.append_child_value("label", channel.label)
        node.append_child_value("unit", "uV")
        node.append_child_value("type", "EEG")
    info.desc().append_child_value("manufacturer", "imagined-speech synthetic")
    outlet = pylsl.StreamOutlet(info, chunk_size=1, max_buffered=360)

    started = time.perf_counter()
    next_sample = started
    sample_index = 0
    try:
        while duration_seconds is None or time.perf_counter() - started < duration_seconds:
            now = time.perf_counter()
            if now < next_sample:
                time.sleep(min(next_sample - now, 0.01))
                continue
            sample = synthetic_eeg_sample(
                sample_index,
                profile.sampling_rate_hz,
                len(profile.eeg_channels),
            )
            outlet.push_sample(sample, pylsl.local_clock())
            sample_index += 1
            next_sample = started + sample_index / profile.sampling_rate_hz
    except KeyboardInterrupt:
        pass
    return sample_index


def load_default_lsl_profile() -> DeviceProfile:
    return load_device_profile(default_lsl_profile_path())
