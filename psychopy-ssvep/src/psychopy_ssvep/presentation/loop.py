"""PsychoPy presentation loop driving the session coordinator."""

from __future__ import annotations

import sys
import time

from psychopy import core, event

from psychopy_ssvep.config.loader import ResolvedExperiment
from psychopy_ssvep.presentation.renderer import PsychopyRenderer, create_window
from psychopy_ssvep.runtime.commands import AbortSession, FrameKind, PresentationFrameAcknowledged
from psychopy_ssvep.runtime.coordinator import CoordinatorState, SessionCoordinator


def _preflight(
    renderer: PsychopyRenderer, resolved: ResolvedExperiment
) -> float:
    """Measure the real frame rate and enforce timing-quality limits."""
    presentation = resolved.config.presentation
    target = presentation.psychopy.refresh_rate_hz
    warmup = 10
    for _ in range(warmup):
        renderer.window.flip()
    start = time.perf_counter()
    for _ in range(presentation.refresh_preflight_frames):
        renderer.window.flip()
    elapsed = time.perf_counter() - start
    frames = presentation.refresh_preflight_frames
    actual = frames / elapsed if elapsed > 0 else 0.0
    expected = elapsed * target
    dropped = max(0.0, 1.0 - frames / expected) if expected > 0 else 0.0
    error = abs(actual - target)
    quality_ok = actual > 0 and error <= presentation.max_frequency_error_hz
    drops_ok = dropped <= presentation.max_dropped_frame_fraction
    if presentation.require_timing_quality and (not quality_ok or not drops_ok):
        renderer.window.close()
        raise RuntimeError(
            f"display timing quality check failed: measured {actual:.2f} Hz "
            f"(target {target:g} Hz, error {error:.2f} Hz), dropped-frame "
            f"fraction {dropped:.3%}"
        )
    if not quality_ok or not drops_ok:
        print(
            f"warning: display timing off target (measured {actual:.2f} Hz, "
            f"target {target:g} Hz, dropped {dropped:.3%})",
            file=sys.stderr,
        )
    return actual


def run_session(
    resolved: ResolvedExperiment,
    participant_id: str,
    session_label: str | None = None,
    *,
    windowed: bool = False,
) -> int:
    config = resolved.config
    presentation = config.presentation
    window = create_window(presentation, force_windowed=windowed)
    renderer = PsychopyRenderer(window, presentation)
    n_frames = int(round(
        presentation.psychopy.refresh_rate_hz * config.protocol.stimulation_seconds
    ))
    renderer.precompute_waves(config.stimuli, n_frames)

    coordinator: SessionCoordinator | None = None
    try:
        _preflight(renderer, resolved)
        coordinator = SessionCoordinator(resolved, participant_id, session_label)
        coordinator.start()
        while coordinator.state not in {
            CoordinatorState.FINALIZED, CoordinatorState.FAILED
        }:
            if event.getKeys(["escape"]):
                coordinator.execute(AbortSession("Escape pressed"))
            coordinator.tick()
            if coordinator.state in {
                CoordinatorState.FINALIZED, CoordinatorState.FAILED
            }:
                break
            view = coordinator.runtime.view_state()
            renderer.set_view_state(view)
            renderer.draw(view)
            window.flip()
            pending = renderer.consume_pending_ack()
            if pending is not None and view.presentation_id is not None:
                coordinator.execute(PresentationFrameAcknowledged(
                    presentation_id=view.presentation_id,
                    kind=FrameKind(pending),
                    monotonic_timestamp=coordinator.clock.monotonic(),
                    wall_clock_timestamp_utc=coordinator.clock.wall_time_utc(),
                ))
            renderer.advance_wave()
    except Exception as exc:
        if coordinator is not None and coordinator.state not in {
            CoordinatorState.FINALIZED, CoordinatorState.FAILED
        }:
            coordinator.close()
        window.close()
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        core.quit()

    window.close()
    print(f"session recorded at: {coordinator.session_path}")
    if coordinator.validation_report:
        print(
            "validation: "
            + ", ".join(
                f"{key}={value}" for key, value in coordinator.validation_report.items()
            )
        )
    return 0 if coordinator.state == CoordinatorState.FINALIZED else 1
