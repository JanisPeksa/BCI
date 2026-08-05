from dataclasses import dataclass
import sys

import pytest

from imagined_speech.displays import NativeDisplay, enumerate_native_displays
from imagined_speech.ui.display_selection import (
    build_subject_display_targets,
    display_target_label,
)


@dataclass(frozen=True)
class _Geometry:
    left: int
    top: int
    width_value: int
    height_value: int

    def x(self) -> int:
        return self.left

    def y(self) -> int:
        return self.top

    def width(self) -> int:
        return self.width_value

    def height(self) -> int:
        return self.height_value


class _Screen:
    def __init__(self, name: str, geometry: tuple[int, int, int, int]) -> None:
        self._name = name
        self._geometry = _Geometry(*geometry)

    def name(self) -> str:
        return self._name

    def geometry(self) -> _Geometry:
        return self._geometry


def test_native_order_maps_reversed_qt_displays_to_psychopy_indexes() -> None:
    qt_screens = [
        _Screen("DELL S3422DW", (0, 0, 3440, 1440)),
        _Screen(r"\\.\DISPLAY1", (-1920, 1045, 1920, 1080)),
    ]
    native = [
        NativeDisplay(0, r"\\.\DISPLAY1", -1920, 1045, 1920, 1080, False),
        NativeDisplay(1, r"\\.\DISPLAY2", 0, 0, 3440, 1440, True),
    ]

    targets = build_subject_display_targets(qt_screens, native)

    assert targets[0].device_name == r"\\.\DISPLAY1"
    assert targets[0].psychopy_index == 0
    assert targets[0].qt_index == 1
    assert targets[1].device_name == r"\\.\DISPLAY2"
    assert targets[1].psychopy_index == 1
    assert targets[1].qt_index == 0
    assert "DELL S3422DW" in display_target_label(targets[1])
    assert r"\\.\DISPLAY2" in display_target_label(targets[1])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows display contract")
def test_native_enumeration_isolated_from_pyglet_ctypes_signatures() -> None:
    import pyglet  # noqa: F401 - importing mutates Pyglet's user32 function signatures

    displays = enumerate_native_displays()

    assert displays
    assert all(display.device_name.startswith(r"\\.\DISPLAY") for display in displays)
