"""Human-readable experiment configuration previews."""

from __future__ import annotations

from imagined_speech.config import ResolvedExperiment


def format_duration(total_seconds: float) -> str:
    rounded = int(round(total_seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render_preview(resolved: ResolvedExperiment) -> str:
    config = resolved.config
    audio_assets = sum("audio" in assets for assets in resolved.assets.values())
    image_assets = sum("image" in assets for assets in resolved.assets.values())
    image_assets += int("speaking_image" in resolved.presentation_assets)
    phases = " -> ".join(
        f"{phase.value.upper()} ({config.phases[phase].duration_seconds:g}s)"
        for phase in config.phase_sequence
    )
    if config.protocol.post_trial_seconds > 0:
        phases += f" -> POST_TRIAL ({config.protocol.post_trial_seconds:g}s)"
    break_count = max(0, config.protocol.experiment.blocks - 1)
    audio_state = "enabled" if config.presentation.audio.enabled else "disabled"

    lines = [
        f"Experiment: {config.title} [{config.experiment_id}]",
        f"Configuration: {resolved.config_path}",
        f"Profile: {config.profile.value}",
        f"Random seed: {config.random_seed}",
        f"Device: {resolved.device.profile_id} ({resolved.device.backend}, "
        f"{resolved.device.sampling_rate_hz:g} Hz, "
        f"{len(resolved.device.eeg_channels)} EEG channels)",
        f"Montage: {', '.join(channel.label for channel in resolved.device.eeg_channels)}",
        f"Stimuli: {len(config.stimuli)}",
        f"Recorded trials: {config.recorded_trials} "
        f"({config.protocol.experiment.blocks} blocks x {config.trials_per_block})",
        f"Practice: {config.protocol.practice.blocks} block(s), "
        f"{config.practice_trials} trial(s)",
        f"Phases: {phases}",
        f"Trial duration: {format_duration(config.trial_duration_seconds)}",
        f"Breaks: {break_count} x "
        f"{format_duration(config.protocol.inter_block_break_seconds)}",
        f"Initial/final rest: {format_duration(config.protocol.initial_rest_seconds)} / "
        f"{format_duration(config.protocol.final_rest_seconds)}",
        f"Subject window mode: {config.presentation.psychopy.window_mode.value}",
        f"Audio: {audio_state} ({audio_assets} configured assets)",
        f"Images: {image_assets} configured assets",
        f"Projected duration: {format_duration(config.projected_duration_seconds)}",
    ]
    return "\n".join(lines)
