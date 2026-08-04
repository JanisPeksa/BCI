from imagined_speech.presentation.renderer import _set_text_if_changed


class _Stimulus:
    def __init__(self, text: str) -> None:
        self._text = text
        self.assignments = 0

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self.assignments += 1
        self._text = value


def test_unchanged_text_does_not_rebuild_stimulus() -> None:
    stimulus = _Stimulus("Think")

    _set_text_if_changed(stimulus, "Think")
    _set_text_if_changed(stimulus, "Rest")

    assert stimulus.assignments == 1
    assert stimulus.text == "Rest"
