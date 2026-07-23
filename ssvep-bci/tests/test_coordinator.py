from __future__ import annotations

import time

from ssvep_bci.config import load_experiment
from ssvep_bci.config.loader import ResolvedExperiment
from ssvep_bci.recording import validate_session
from ssvep_bci.runtime.clock import VirtualClock
from ssvep_bci.runtime.commands import FrameKind, PresentationFrameAcknowledged
from ssvep_bci.runtime.coordinator import CoordinatorState, SessionCoordinator
from ssvep_bci.runtime.protocol import RunState


def test_virtual_coordinator_records_complete_session(tmp_path) -> None:
    original = load_experiment()
    resolved = ResolvedExperiment(
        config=original.config,
        config_path=original.config_path,
        device=original.device,
        device_path=original.device_path,
        output_root=tmp_path,
        assets=original.assets,
        classifier_path=original.classifier_path,
    )
    clock = VirtualClock()
    coordinator = SessionCoordinator(resolved, "SIM001", clock=clock)
    coordinator.start()
    clock.advance(1)
    time.sleep(0.03)
    coordinator.tick()

    # Initial rest + first pre-stimulus phase.
    clock.advance(2)
    time.sleep(0.03)
    coordinator.tick()
    for trial in range(2):
        assert coordinator.runtime.state == RunState.AWAITING_ONSET
        presentation = coordinator.runtime.current_step.presentation_id
        coordinator.execute(PresentationFrameAcknowledged(
            presentation,
            FrameKind.ONSET,
            clock.monotonic(),
            clock.wall_time_utc(),
        ))
        time.sleep(0.03)
        clock.advance(5)
        time.sleep(0.08)
        coordinator.tick()
        assert coordinator.runtime.state == RunState.AWAITING_OFFSET
        coordinator.execute(PresentationFrameAcknowledged(
            presentation,
            FrameKind.OFFSET,
            clock.monotonic(),
            clock.wall_time_utc(),
        ))
        time.sleep(0.03)
        if trial == 0:
            clock.advance(2)  # inter-trial + next pre-stimulus
            time.sleep(0.03)
            coordinator.tick()

    clock.advance(1)  # final rest
    time.sleep(0.03)
    coordinator.tick()
    assert coordinator.state == CoordinatorState.POST_ROLL
    clock.advance(1)  # acquisition post-roll
    time.sleep(0.08)
    coordinator.tick()
    assert coordinator.state == CoordinatorState.FINALIZED
    report = validate_session(coordinator.session_path)
    assert report["status"] == "complete"
    assert report["sample_count"] > 0
