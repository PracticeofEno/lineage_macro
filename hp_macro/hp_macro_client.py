"""
hp_macro_client.py - minimal HP macro client.

Only does these things:
  1. Keep the client window as the action target.
  2. On server request, press F5 and click once.
  3. Monitor its own HP; if below 50%, press F5 twice.
  4. Left-click periodically using a normal-distribution delay around 2 seconds.

It intentionally does NOT press F8 or run start/stop hold loops.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import win32con
import win32gui

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro

SERVER_HOST = "papawolf16"
SERVER_PORT = 9997
RECONNECT_DELAY_SECONDS = 5.0
WINDOW_TITLE = "client"
POLL_INTERVAL_SECONDS = 0.5
HP_PERCENT_THRESHOLD = 50.0
HP_HEAL_COOLDOWN_SECONDS = 3.0
CLICK_DELAY_START_MEAN_SECONDS = 2.0
CLICK_DELAY_TARGET_MEAN_SECONDS = 4.0
CLICK_DELAY_RAMP_SECONDS = 600.0
CLICK_DELAY_STDDEV_SECONDS = 0.45
CLICK_DELAY_MIN_SECONDS = 1.0
CLICK_DELAY_MAX_SECONDS = 5.5

HP_READ: dict[str, Any] = {
    "x": 976,
    "y": 71,
    "width": 80,
    "height": 21,
    "color_rgb": (247, 201, 227),
    "x_offsets": [0, 5, 10],
    "text_x_offsets": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "text_y_offsets": [0, 1, 2, 3, 4, 5],
}

# Client self-heal only needs a percent threshold.  The character-info
# HP text can move or use a glyph that macro.read_text cannot decode, so use
# the stable bottom HP bar as a fallback.  Coordinates are in the screenshot
# frame produced by macro.screenshot() / the debug PNG.
HP_BAR_READ: dict[str, int] = {
    "x": 288,
    "y": 665,
    "width": 286,
    "height": 28,
}

_recv_buffers: dict[socket.socket, bytes] = {}
_running = True
_current_conn: socket.socket | None = None
_action_lock = threading.Lock()


def _visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return windows


def _print_visible_windows() -> None:
    print("[hp_macro_client] visible windows:")
    for hwnd, title in _visible_windows():
        print(f"  hwnd={hwnd} title={title!r}")


def _find_window(title: str) -> int:
    title_lower = title.lower()
    windows = _visible_windows()

    for hwnd, window_title in windows:
        if window_title.lower() == title_lower:
            return hwnd
    for hwnd, window_title in windows:
        if window_title.lower().startswith(title_lower):
            return hwnd
    for hwnd, window_title in windows:
        if title_lower in window_title.lower():
            return hwnd

    # If the client window has not been renamed yet, use a visible Lineage
    # Classic window. Prefer one that is not already the server window.
    if title_lower == "client":
        lineage_windows = [
            (hwnd, window_title)
            for hwnd, window_title in windows
            if window_title.lower().startswith("lineage classic")
        ]
        for hwnd, window_title in lineage_windows:
            if "server" not in window_title.lower():
                print(f"[hp_macro_client] using fallback game window: {window_title!r}")
                return hwnd
        if lineage_windows:
            hwnd, window_title = lineage_windows[0]
            print(f"[hp_macro_client] using fallback game window: {window_title!r}")
            return hwnd

    _print_visible_windows()
    raise RuntimeError(f"window not found: {title!r}")


def _init_target_window(title: str, hwnd: int | None = None) -> None:
    target_hwnd = hwnd if hwnd is not None else _find_window(title)
    current_title = win32gui.GetWindowText(target_hwnd)
    macro.set_hwnd(target_hwnd)

    # Name the fallback window "client" for the next run, but do not move it.
    if title and current_title.lower() != title.lower() and title.lower() == "client":
        try:
            win32gui.SetWindowText(target_hwnd, title)
            print(f"[hp_macro_client] renamed window {current_title!r} -> {title!r}")
        except OSError as exc:
            print(f"[hp_macro_client] could not rename window: {exc}")

    # Do not force foreground on startup/--once. Actions call foreground right before F5/click.


def _send_json(conn: socket.socket, obj: dict[str, Any]) -> bool:
    try:
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def _recv_json(conn: socket.socket) -> dict[str, Any] | None:
    buf = _recv_buffers.pop(conn, b"")
    try:
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        line, rest = buf.split(b"\n", 1)
        if rest:
            _recv_buffers[conn] = rest
        return json.loads(line.decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stat_text(cfg: dict[str, Any], img: Any) -> str:
    x, y = int(cfg["x"]), int(cfg["y"])
    w, h = int(cfg["width"]), int(cfg["height"])
    color = tuple(int(v) for v in cfg["color_rgb"])
    best = ""

    for dx in cfg.get("x_offsets", [0]):
        cropped = macro.crop(img, x + int(dx), y, w, h)
        for ty in cfg.get("text_y_offsets", [0]):
            for tx in cfg.get("text_x_offsets", [0]):
                text = macro.read_text(cropped, int(tx), int(ty), color)
                if len(text) > len(best):
                    best = text
    return best


def _parse_stat(text: str) -> dict[str, float | int] | None:
    if "/" not in text:
        return None
    cur_str, max_str = text.split("/", 1)
    cur_digits = "".join(c for c in cur_str if c.isdigit())
    max_digits = "".join(c for c in max_str if c.isdigit())
    if not cur_digits or not max_digits:
        return None

    current = int(cur_digits)
    maximum = int(max_digits)
    if maximum <= 0:
        return None

    return {"current": current, "maximum": maximum, "percent": current / maximum * 100.0}


def _read_hp_bar_percent(img: Any) -> float | None:
    cfg = HP_BAR_READ
    x = int(cfg["x"])
    y = int(cfg["y"])
    width = int(cfg["width"])
    height = int(cfg["height"])
    crop = macro.crop(img, x, y, width, height).convert("RGB")

    filled_columns = 0
    usable_columns = 0
    for col in range(width):
        red_pixels = 0
        sampled_pixels = 0
        for row in range(2, max(2, height - 2)):
            r, g, b = crop.getpixel((col, row))
            # Red HP fill: strong red, low green/blue.  This ignores white text
            # and the frame while still detecting the bar body.
            if r >= 85 and g <= 85 and b <= 85 and r >= g + 25 and r >= b + 25:
                red_pixels += 1
            sampled_pixels += 1
        if sampled_pixels == 0:
            continue
        usable_columns += 1
        if red_pixels / sampled_pixels >= 0.25:
            filled_columns += 1

    if usable_columns <= 0:
        return None
    percent = filled_columns / usable_columns * 100.0
    return max(0.0, min(100.0, percent))


def read_hp_state(img: Any) -> dict[str, float | int | str] | None:
    text_state = _parse_stat(_read_stat_text(HP_READ, img))
    if text_state is not None:
        text_state["source"] = "text"
        return text_state

    bar_percent = _read_hp_bar_percent(img)
    if bar_percent is None:
        return None
    return {"current": round(bar_percent), "maximum": 100, "percent": bar_percent, "source": "bar"}


def _fmt_hp(state: dict[str, float | int | str] | None) -> str:
    if state is None:
        return "HP=read failed"
    source = state.get("source", "text")
    return f"HP={state['current']}/{state['maximum']} ({state['percent']:.1f}%, {source})"


def _current_click_mean(start_time: float, now: float | None = None) -> float:
    if now is None:
        now = time.time()
    elapsed = max(0.0, now - start_time)
    if CLICK_DELAY_RAMP_SECONDS <= 0:
        return CLICK_DELAY_TARGET_MEAN_SECONDS

    progress = min(1.0, elapsed / CLICK_DELAY_RAMP_SECONDS)
    return CLICK_DELAY_START_MEAN_SECONDS + (
        CLICK_DELAY_TARGET_MEAN_SECONDS - CLICK_DELAY_START_MEAN_SECONDS
    ) * progress


def _random_click_delay(mean_seconds: float) -> float:
    delay = random.gauss(mean_seconds, CLICK_DELAY_STDDEV_SECONDS)
    return max(CLICK_DELAY_MIN_SECONDS, min(CLICK_DELAY_MAX_SECONDS, delay))


def _press_f5_locked() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F5)


def _left_click_locked() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_mouse_click_left()


def _f5_click() -> None:
    with _action_lock:
        _press_f5_locked()
        time.sleep(0.1)
        macro.arduino_mouse_click_left()


def _self_heal() -> None:
    with _action_lock:
        _press_f5_locked()
        time.sleep(0.1)
        _press_f5_locked()


def _periodic_left_click() -> None:
    with _action_lock:
        _left_click_locked()


def _handle_command(msg: dict[str, Any]) -> dict[str, Any] | None:
    cmd = msg.get("cmd")
    req_id = msg.get("req_id")

    if cmd == "ping":
        return {"status": "pong", "req_id": req_id}

    # hold_f5 is kept only as a legacy alias for older server builds.
    if cmd in {"f5_click", "hold_f5"}:
        print("[hp_macro_client] server request -> F5 + click")
        _f5_click()
        return {"status": "ok", "req_id": req_id}

    print(f"[hp_macro_client] ignoring unsupported command: {msg}")
    return {"status": "ignored", "req_id": req_id, "cmd": cmd}


def _run_connection(conn: socket.socket) -> None:
    global _current_conn
    _current_conn = conn
    print("[hp_macro_client] connected to server")
    while _running:
        msg = _recv_json(conn)
        if msg is None:
            print("[hp_macro_client] server disconnected")
            break
        resp = _handle_command(msg)
        if resp is not None and not _send_json(conn, resp):
            print("[hp_macro_client] failed to send response")
            break
    _current_conn = None


def _connect_loop() -> None:
    while _running:
        conn: socket.socket | None = None
        try:
            print(f"[hp_macro_client] connecting: {SERVER_HOST}:{SERVER_PORT}")
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((SERVER_HOST, SERVER_PORT))
            _run_connection(conn)
        except (ConnectionRefusedError, OSError) as exc:
            print(f"[hp_macro_client] connection failed: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
                _recv_buffers.pop(conn, None)

        if _running:
            print(f"[hp_macro_client] reconnecting in {RECONNECT_DELAY_SECONDS:.0f}s")
            time.sleep(RECONNECT_DELAY_SECONDS)


def _self_hp_monitor_loop() -> None:
    last_status_time = 0.0
    last_heal_time = 0.0
    click_ramp_start_time = time.time()
    next_click_time = click_ramp_start_time + _random_click_delay(
        _current_click_mean(click_ramp_start_time, click_ramp_start_time)
    )

    print(
        "[hp_macro_client] running: server F5+click requests, "
        "self HP<50% F5x2, periodic left click mean ramps 2.0s->4.0s"
    )

    while _running:
        now = time.time()
        img = macro.screenshot()
        hp_state = read_hp_state(img)

        if now - last_status_time >= 1.0:
            print(f"[hp_macro_client] {_fmt_hp(hp_state)}")
            last_status_time = now

        if hp_state is not None:
            hp_low = float(hp_state["percent"]) < HP_PERCENT_THRESHOLD
            cooldown_ready = now - last_heal_time >= HP_HEAL_COOLDOWN_SECONDS
            if hp_low and cooldown_ready:
                print(f"[hp_macro_client] self HP {hp_state['percent']:.1f}% < 50.0% -> F5 x2")
                _self_heal()
                last_heal_time = time.time()

        now = time.time()
        if now >= next_click_time:
            mean = _current_click_mean(click_ramp_start_time, now)
            delay = _random_click_delay(mean)
            print(
                f"[hp_macro_client] periodic left click; "
                f"mean={mean:.2f}s next in {delay:.2f}s"
            )
            _periodic_left_click()
            next_click_time = time.time() + delay

        time.sleep(POLL_INTERVAL_SECONDS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal HP macro client.")
    parser.add_argument("--title", default=WINDOW_TITLE, help="Window title to control. Default: client")
    parser.add_argument("--hwnd", type=int, default=None, help="Explicit window handle to control.")
    parser.add_argument("--host", default=SERVER_HOST, help="Server host. Default: papawolf16")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help="Server port. Default: 9997")
    parser.add_argument("--once", action="store_true", help="Read client HP once and exit.")
    parser.add_argument("--list-windows", action="store_true", help="List visible windows and exit.")
    return parser.parse_args()


def main() -> int:
    global SERVER_HOST, SERVER_PORT, _running

    args = parse_args()
    SERVER_HOST = args.host
    SERVER_PORT = args.port

    if args.list_windows:
        _print_visible_windows()
        return 0

    _init_target_window(args.title, args.hwnd)

    if args.once:
        img = macro.screenshot()
        hp_text = _read_stat_text(HP_READ, img)
        bar_percent = _read_hp_bar_percent(img)
        print(
            f"[hp_macro_client] HP raw={hp_text!r}  "
            f"bar={bar_percent if bar_percent is not None else 'read failed'}  "
            f"{_fmt_hp(read_hp_state(img))}"
        )
        return 0

    threading.Thread(target=_connect_loop, daemon=True).start()

    try:
        _self_hp_monitor_loop()
    except KeyboardInterrupt:
        print("\n[hp_macro_client] stopping")
    finally:
        _running = False
        if _current_conn is not None:
            try:
                _current_conn.close()
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
