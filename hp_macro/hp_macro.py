import argparse
import sys
import time
from pathlib import Path
from typing import Any

import win32con
import win32gui

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro

WINDOW_TITLE = "server"
HP_PERCENT_THRESHOLD = 60.0
MP_THRESHOLD = 50  # MP 현재값(절대값) 기준. 이 값 미만이면 F8.
POLL_INTERVAL_SECONDS = 0.2
F5_HOLD_SECONDS = 1.0
F8_COOLDOWN_SECONDS = 600.0
TRIGGER_COOLDOWN_SECONDS = 2.5
STATUS_INTERVAL_SECONDS = 1.0

CONFIG: dict[str, Any] = {
    "window_title": WINDOW_TITLE,
    "hp_percent_threshold": HP_PERCENT_THRESHOLD,
    "mp_threshold": MP_THRESHOLD,
    "poll_interval_seconds": POLL_INTERVAL_SECONDS,
    "f5_hold_seconds": F5_HOLD_SECONDS,
    "f8_cooldown_seconds": F8_COOLDOWN_SECONDS,
    "trigger_cooldown_seconds": TRIGGER_COOLDOWN_SECONDS,
    "status_interval_seconds": STATUS_INTERVAL_SECONDS,
}


def find_window(title_prefix: str | None) -> int:
    windows: list[tuple[str, int]] = []

    def callback(hwnd: int, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((title, hwnd))

    win32gui.EnumWindows(callback, None)

    if title_prefix:
        for title, hwnd in windows:
            if title.startswith(title_prefix):
                return hwnd
        raise RuntimeError(f"window not found: {title_prefix}")

    for preferred in ("client", "server"):
        for title, hwnd in windows:
            if title == preferred:
                return hwnd

    for title, hwnd in windows:
        if title.startswith("Lineage Classic"):
            return hwnd

    raise RuntimeError("Lineage Classic window not found")


def hold_f5(seconds: float) -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_down(win32con.VK_F5)
    try:
        time.sleep(seconds)
    finally:
        macro.arduino_key_up(win32con.VK_F5)


def press_f8() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F8)


def init_window(config: dict[str, Any], title_override: str | None) -> None:
    title = title_override if title_override is not None else str(config.get("window_title", "client"))
    macro.set_hwnd(find_window(title))


def run(config: dict[str, Any], once: bool = False) -> None:
    hp_threshold = float(config.get("hp_percent_threshold", 50.0))
    mp_threshold = float(config.get("mp_threshold", 50))
    poll_interval = float(config.get("poll_interval_seconds", 0.2))
    hold_seconds = float(config.get("f5_hold_seconds", 1.5))
    f8_cooldown = float(config.get("f8_cooldown_seconds", 600.0))
    cooldown = float(config.get("trigger_cooldown_seconds", hold_seconds))
    status_interval = float(config.get("status_interval_seconds", 1.0))
    last_trigger_time = 0.0
    last_status_time = 0.0
    last_f8_time = 0.0

    print(
        f"[hp_macro] start hp_threshold={hp_threshold:.1f}%, mp_threshold={mp_threshold:.0f}, "
        f"hold={hold_seconds}s, poll={poll_interval}s, cooldown={cooldown}s, "
        f"f8_cooldown={f8_cooldown}s"
    )

    while True:
        img = macro.screenshot()
        current_hp, max_hp = macro.read_hp(img)
        mp = macro.read_mp(img)
        now = time.time()

        hp_percent = current_hp / max_hp * 100.0 if max_hp > 0 else None

        if now - last_status_time >= status_interval:
            if hp_percent is None:
                print(f"[hp_macro] HP read failed, MP={mp}")
            else:
                print(f"[hp_macro] HP={current_hp}/{max_hp} ({hp_percent:.1f}%), MP={mp}")
            last_status_time = now

        if once:
            return

        if mp < mp_threshold and now - last_f8_time >= f8_cooldown:
            print(f"[hp_macro] MP {mp} < {mp_threshold:.0f} -> press F8")
            press_f8()
            last_f8_time = time.time()

        if hp_percent is not None and hp_percent < hp_threshold and now - last_trigger_time >= cooldown:
            print(f"[hp_macro] HP {hp_percent:.1f}% < {hp_threshold:.1f}% -> hold F5 {hold_seconds}s")
            hold_f5(hold_seconds)
            last_trigger_time = time.time()

        time.sleep(poll_interval)



def main() -> int:
    macro.init_setting("server")
    config = dict(CONFIG)

    try:
        run(config)
    except KeyboardInterrupt:
        print("\n[hp_macro] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
