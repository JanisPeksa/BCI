"""Build stable subject-display choices for the experimenter setup UI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from imagined_speech.displays import NativeDisplay, enumerate_native_displays
from imagined_speech.ipc.messages import SubjectDisplayTargetPayload


def _qt_geometry(screen: Any) -> tuple[int, int, int, int]:
    geometry = screen.geometry()
    return (
        int(geometry.x()),
        int(geometry.y()),
        int(geometry.width()),
        int(geometry.height()),
    )


def _overlap_area(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> int:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    width = max(
        0,
        min(first_x + first_width, second_x + second_width)
        - max(first_x, second_x),
    )
    height = max(
        0,
        min(first_y + first_height, second_y + second_height)
        - max(first_y, second_y),
    )
    return width * height


def _matching_qt_index(
    display: NativeDisplay, screens: Sequence[Any]
) -> int | None:
    geometry = display.geometry
    exact = [
        index for index, screen in enumerate(screens) if _qt_geometry(screen) == geometry
    ]
    if exact:
        return exact[0]
    overlaps = [
        (_overlap_area(geometry, _qt_geometry(screen)), index)
        for index, screen in enumerate(screens)
    ]
    best_area, best_index = max(overlaps, default=(0, -1))
    return best_index if best_area > 0 else None


def build_subject_display_targets(
    screens: Sequence[Any],
    native_displays: Sequence[NativeDisplay] | None = None,
) -> tuple[SubjectDisplayTargetPayload, ...]:
    native = tuple(
        enumerate_native_displays() if native_displays is None else native_displays
    )
    if not native:
        primary = screens[0] if screens else None
        return tuple(
            SubjectDisplayTargetPayload(
                psychopy_index=index,
                qt_index=index,
                qt_name=str(screen.name()),
                geometry=_qt_geometry(screen),
                primary=screen is primary,
            )
            for index, screen in enumerate(screens)
        )

    targets: list[SubjectDisplayTargetPayload] = []
    for display in native:
        qt_index = _matching_qt_index(display, screens)
        qt_name = str(screens[qt_index].name()) if qt_index is not None else ""
        targets.append(SubjectDisplayTargetPayload(
            device_name=display.device_name,
            psychopy_index=display.index,
            qt_index=qt_index if qt_index is not None else display.index,
            qt_name=qt_name,
            geometry=display.geometry,
            primary=display.primary,
        ))
    return tuple(targets)


def display_target_label(target: SubjectDisplayTargetPayload) -> str:
    identity = target.device_name or f"PsychoPy screen {target.psychopy_index}"
    if target.qt_name and target.qt_name.casefold() != identity.casefold():
        identity = f"{target.qt_name} / {identity}"
    width, height = target.geometry[2:]
    primary = ", primary" if target.primary else ""
    return f"{identity} ({width}x{height}{primary})"
