from imagined_speech.presentation.audio import AudioScheduler


class _Sound:
    def __init__(self, events: list[tuple[str, float | None]]) -> None:
        self.events = events

    def play(self, *, when: float) -> None:
        self.events.append(("play", when))

    def stop(self) -> None:
        self.events.append(("stop", None))


class _Window:
    def getFutureFlipTime(self, *, clock: str) -> float:  # noqa: N802 - PsychoPy API
        assert clock == "ptb"
        return 12.5


def test_audio_plays_only_for_stimulus_and_stops_at_next_phase() -> None:
    events: list[tuple[str, float | None]] = []
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)
    scheduler._sounds = {"p": _Sound(events)}
    window = _Window()

    assert scheduler.transition({"screen": "rest", "stimulus_id": "p"}, window) == (
        None,
        False,
    )
    assert scheduler.transition(
        {"screen": "stimulus", "stimulus_id": "p"}, window
    ) == (12.5, True)
    assert events == [("play", 12.5)]

    assert scheduler.transition(
        {"screen": "fixation", "stimulus_id": "p"}, window
    ) == (None, False)
    assert events == [("play", 12.5), ("stop", None)]


def test_restarting_stimulus_stops_previous_playback_first() -> None:
    events: list[tuple[str, float | None]] = []
    scheduler = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)
    scheduler._sounds = {"p": _Sound(events)}
    view = {"screen": "stimulus", "stimulus_id": "p"}
    window = _Window()

    scheduler.transition(view, window)
    scheduler.transition(view, window)

    assert events == [("play", 12.5), ("stop", None), ("play", 12.5)]


def test_disabled_or_missing_audio_never_starts() -> None:
    window = _Window()
    disabled = AudioScheduler(enabled=False, volume=0.8, latency_mode=3)
    enabled = AudioScheduler(enabled=True, volume=0.8, latency_mode=3)

    assert disabled.transition(
        {"screen": "stimulus", "stimulus_id": "p"}, window
    ) == (None, False)
    assert enabled.transition(
        {"screen": "stimulus", "stimulus_id": "missing"}, window
    ) == (None, False)
