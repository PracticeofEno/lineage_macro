from __future__ import annotations

import time
from ctypes import windll

import win32con
import win32gui

LINEAGE_WINDOW_TITLE_PREFIX = "Lineage Classic"


def enum_visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return windows


class WindowTarget:
    def __init__(self, title_prefix: str):
        self._title_prefix = title_prefix
        self._title_prefix_lower = title_prefix.casefold()
        self._hwnd: int | None = None

    def resolve(self) -> tuple[int, str]:
        if self._hwnd is not None and win32gui.IsWindow(self._hwnd):
            title = win32gui.GetWindowText(self._hwnd)
            if title and title.casefold().startswith(self._title_prefix_lower):
                return self._hwnd, title

        matches = [
            (hwnd, title)
            for hwnd, title in enum_visible_windows()
            if title.casefold().startswith(self._title_prefix_lower)
        ]
        if not matches:
            raise RuntimeError(f"'{self._title_prefix}' window not found")

        exact = [entry for entry in matches if entry[1].casefold() == self._title_prefix_lower]
        hwnd, title = exact[0] if exact else matches[0]
        self._hwnd = hwnd
        return hwnd, title


class RenameableWindowTarget:
    def __init__(
        self,
        title_prefix: str,
        *,
        auto_rename_lineage: bool = True,
        lineage_title_prefix: str = LINEAGE_WINDOW_TITLE_PREFIX,
    ):
        self._title_prefix = title_prefix
        self._title_prefix_lower = title_prefix.casefold()
        self._auto_rename_lineage = auto_rename_lineage
        self._lineage_title_prefix = lineage_title_prefix
        self._hwnd: int | None = None

    def resolve(self) -> tuple[int, str]:
        if self._hwnd is not None and win32gui.IsWindow(self._hwnd):
            title = win32gui.GetWindowText(self._hwnd)
            if title and title.casefold().startswith(self._title_prefix_lower):
                return self._hwnd, title

        windows = enum_visible_windows()
        exact_match = self._find_title_match(windows, exact_only=True)
        if exact_match is not None:
            self._hwnd, title = exact_match
            return self._hwnd, title

        if self._auto_rename_lineage:
            lineage_match = self._find_lineage_window(windows)
            if lineage_match is not None:
                hwnd, old_title = lineage_match
                win32gui.SetWindowText(hwnd, self._title_prefix)
                time.sleep(0.05)
                title = win32gui.GetWindowText(hwnd) or self._title_prefix
                self._hwnd = hwnd
                print(f"[window] renamed '{old_title}' -> '{title}'")
                return self._hwnd, title

        prefix_match = self._find_title_match(windows, exact_only=False)
        if prefix_match is not None:
            self._hwnd, title = prefix_match
            return self._hwnd, title

        raise RuntimeError(f"window not found: {self._title_prefix}")

    def _find_title_match(
        self,
        windows: list[tuple[int, str]],
        exact_only: bool,
    ) -> tuple[int, str] | None:
        if exact_only:
            for hwnd, title in windows:
                if title.casefold() == self._title_prefix_lower:
                    return hwnd, title
            return None

        matches = [
            (hwnd, title)
            for hwnd, title in windows
            if title.casefold().startswith(self._title_prefix_lower)
        ]
        if not matches:
            return None
        return matches[0]

    def _find_lineage_window(self, windows: list[tuple[int, str]]) -> tuple[int, str] | None:
        for hwnd, title in windows:
            if title.startswith(self._lineage_title_prefix):
                return hwnd, title
        return None


def focus_window(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    windll.user32.keybd_event(0, 0, 0, 0)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.05)


def move_window_to_origin(hwnd: int) -> tuple[int, int, int, int]:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top
    win32gui.MoveWindow(hwnd, 0, 0, width, height, True)
    time.sleep(0.05)
    return win32gui.GetWindowRect(hwnd)
