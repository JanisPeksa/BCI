from imagined_speech.runtime.clock import RealClock


def test_real_clock_uses_ipc_perf_counter_domain(monkeypatch) -> None:
    monkeypatch.setattr(
        "imagined_speech.runtime.clock.time.perf_counter",
        lambda: 123.5,
    )
    monkeypatch.setattr(
        "imagined_speech.runtime.clock.time.monotonic",
        lambda: 987.0,
    )

    assert RealClock().monotonic() == 123.5
