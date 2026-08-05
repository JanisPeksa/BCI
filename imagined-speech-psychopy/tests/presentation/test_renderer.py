import sys
from types import SimpleNamespace

import pytest

from imagined_speech.displays import NativeDisplay
from imagined_speech.ipc.messages import SubjectDisplayTargetPayload
from imagined_speech.presentation import renderer
from imagined_speech.presentation.renderer import (
    DisplaySelectionError,
    _set_text_if_changed,
    resolve_psychopy_display,
)


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


class _Screen:
    def __init__(
        self, device_name: str, geometry: tuple[int, int, int, int]
    ) -> None:
        self._device_name = device_name
        self.x, self.y, self.width, self.height = geometry

    def get_device_name(self) -> str:
        return self._device_name


def _target(device_name: str = r"\\.\DISPLAY1") -> SubjectDisplayTargetPayload:
    return SubjectDisplayTargetPayload(
        device_name=device_name,
        psychopy_index=1,
        qt_index=1,
        qt_name=device_name,
        geometry=(-1920, 1045, 1920, 1080),
        primary=False,
    )


def test_device_name_overrides_stale_cross_toolkit_index() -> None:
    screens = [
        _Screen(r"\\.\DISPLAY1", (-1920, 1045, 1920, 1080)),
        _Screen(r"\\.\DISPLAY2", (0, 0, 3440, 1440)),
    ]

    resolved = resolve_psychopy_display(screens, 1, _target())

    assert resolved.index == 0
    assert resolved.device_name == r"\\.\DISPLAY1"


def test_missing_named_display_fails_instead_of_falling_back() -> None:
    screens = [_Screen(r"\\.\DISPLAY2", (0, 0, 3440, 1440))]

    with pytest.raises(DisplaySelectionError, match="unavailable") as error:
        resolve_psychopy_display(screens, 0, _target())

    assert error.value.metadata["display"]["verified"] is False
    assert error.value.metadata["display"]["available"][0]["index"] == 0


def test_invalid_configured_index_fails_instead_of_falling_back() -> None:
    screens = [_Screen(r"\\.\DISPLAY2", (0, 0, 3440, 1440))]

    with pytest.raises(DisplaySelectionError, match="screen_index.*4"):
        resolve_psychopy_display(screens, 4, None)


class _Monitor:
    def __init__(self, _name: str) -> None:
        self.gamma = None

    def setGamma(self, value: float) -> None:  # noqa: N802 - PsychoPy API
        self.gamma = value


class _WindowHandle:
    def get_location(self) -> tuple[int, int]:
        return -1920, 1045


class _Window:
    created: list["_Window"] = []

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        self.size = kwargs["size"]
        self._hw_handle = 123
        self.winHandle = _WindowHandle()
        self.closed = False
        self.mouseVisible = True
        self.created.append(self)

    def close(self) -> None:
        self.closed = True


def _window_config(mode: str, size: list[int] | None = None) -> dict:
    return {
        "monitor_name": "testMonitor",
        "gamma": 1.0,
        "window_mode": mode,
        "window_size_px": size,
        "screen_index": 1,
        "hide_cursor": True,
        "use_retina": False,
        "check_timing": True,
        "wait_blanking": True,
    }


def _install_fake_psychopy(monkeypatch: pytest.MonkeyPatch) -> None:
    screens = [
        _Screen(r"\\.\DISPLAY1", (-1920, 1045, 1920, 1080)),
        _Screen(r"\\.\DISPLAY2", (0, 0, 3440, 1440)),
    ]
    fake_pyglet = SimpleNamespace(
        canvas=SimpleNamespace(
            get_display=lambda: SimpleNamespace(get_screens=lambda: screens)
        )
    )
    fake_psychopy = SimpleNamespace(
        monitors=SimpleNamespace(
            getAllMonitors=lambda: ["testMonitor"],
            Monitor=_Monitor,
        ),
        visual=SimpleNamespace(Window=_Window),
    )
    monkeypatch.setitem(sys.modules, "pyglet", fake_pyglet)
    monkeypatch.setitem(sys.modules, "psychopy", fake_psychopy)
    monkeypatch.setattr(
        renderer,
        "native_display_for_window",
        lambda _handle: NativeDisplay(
            0, r"\\.\DISPLAY1", -1920, 1045, 1920, 1080, False
        ),
    )
    _Window.created.clear()


@pytest.mark.parametrize(
    ("mode", "full_screen", "position", "size"),
    [
        ("FULL_SCREEN", True, None, [1920, 1080]),
        ("CENTER", False, None, [1024, 720]),
        ("TOP_LEFT", False, [0, 0], [1024, 720]),
    ],
)
def test_window_modes_use_resolved_display_geometry(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    full_screen: bool,
    position: list[int] | None,
    size: list[int],
) -> None:
    _install_fake_psychopy(monkeypatch)

    window = renderer.create_window(_window_config(mode), _target())

    assert window.kwargs["screen"] == 0
    assert window.kwargs["fullscr"] is full_screen
    assert window.kwargs["pos"] == position
    assert window.kwargs["size"] == size
    assert window._imagined_speech_monitor_metadata["display"]["verified"] is True


def test_oversized_window_is_clamped_to_selected_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_psychopy(monkeypatch)

    window = renderer.create_window(
        _window_config("CENTER", [5000, 3000]), _target()
    )

    assert window.kwargs["size"] == [1920, 1080]
    display = window._imagined_speech_monitor_metadata["display"]
    assert display["window"]["size_clamped"] is True


def test_native_monitor_mismatch_closes_window_and_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_psychopy(monkeypatch)
    monkeypatch.setattr(
        renderer,
        "native_display_for_window",
        lambda _handle: NativeDisplay(1, r"\\.\DISPLAY2", 0, 0, 3440, 1440, True),
    )

    with pytest.raises(DisplaySelectionError, match="different display"):
        renderer.create_window(_window_config("FULL_SCREEN"), _target())

    assert _Window.created[-1].closed is True
