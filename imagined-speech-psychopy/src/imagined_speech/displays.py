"""Native display identities shared by the operator and PsychoPy processes."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeDisplay:
    index: int
    device_name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def geometry(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


def normalize_device_name(value: str | None) -> str:
    return (value or "").strip().casefold()


def enumerate_native_displays() -> tuple[NativeDisplay, ...]:
    """Return Windows displays in the same order used by Pyglet."""

    if sys.platform != "win32":
        return ()

    from ctypes import wintypes

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", Rect),
            ("rcWork", Rect),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(Rect),
        wintypes.LPARAM,
    )
    displays: list[NativeDisplay] = []

    def callback(
        monitor: wintypes.HANDLE,
        _dc: wintypes.HDC,
        _rect: ctypes.POINTER(Rect),
        _data: wintypes.LPARAM,
    ) -> bool:
        info = MonitorInfoEx()
        info.cbSize = ctypes.sizeof(MonitorInfoEx)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return True
        rect = info.rcMonitor
        displays.append(NativeDisplay(
            index=len(displays),
            device_name=str(info.szDevice),
            x=int(rect.left),
            y=int(rect.top),
            width=int(rect.right - rect.left),
            height=int(rect.bottom - rect.top),
            primary=bool(info.dwFlags & 1),
        ))
        return True

    callback_ref = callback_type(callback)
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(Rect),
        callback_type,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MonitorInfoEx),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    if not user32.EnumDisplayMonitors(None, None, callback_ref, 0):
        raise OSError("Windows display enumeration failed")
    return tuple(displays)


def native_display_for_window(window_handle: int) -> NativeDisplay | None:
    """Resolve the native display that owns a Windows window handle."""

    if sys.platform != "win32" or not window_handle:
        return None

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    monitor = user32.MonitorFromWindow(wintypes.HWND(window_handle), 0)
    if not monitor:
        return None

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", Rect),
            ("rcWork", Rect),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    info = MonitorInfoEx()
    info.cbSize = ctypes.sizeof(MonitorInfoEx)
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MonitorInfoEx),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    rect = info.rcMonitor
    device_name = str(info.szDevice)
    index = next(
        (
            display.index
            for display in enumerate_native_displays()
            if normalize_device_name(display.device_name)
            == normalize_device_name(device_name)
        ),
        -1,
    )
    return NativeDisplay(
        index=index,
        device_name=device_name,
        x=int(rect.left),
        y=int(rect.top),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
        primary=bool(info.dwFlags & 1),
    )
