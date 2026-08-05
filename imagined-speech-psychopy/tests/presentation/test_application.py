from imagined_speech.presentation.application import _dropped_batch_since


class _Window:
    refreshThreshold = 0.025

    def __init__(self, intervals: list[float]) -> None:
        self.frameIntervals = intervals


def test_dropped_batch_preserves_clean_intervals_since_previous_delivery() -> None:
    window = _Window([1 / 60, 1 / 60])

    batch, cursor = _dropped_batch_since(window, 0)
    assert batch == ()
    assert cursor == 0

    window.frameIntervals.append(0.1172)
    batch, cursor = _dropped_batch_since(window, cursor)

    assert batch == (1 / 60, 1 / 60, 0.1172)
    assert cursor == 3


def test_dropped_batch_does_not_redeliver_previous_intervals() -> None:
    window = _Window([1 / 60, 0.1172, 1 / 60, 0.028])

    batch, cursor = _dropped_batch_since(window, 2)

    assert batch == (1 / 60, 0.028)
    assert cursor == 4
