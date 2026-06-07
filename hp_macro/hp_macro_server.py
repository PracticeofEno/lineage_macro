"""
hp_macro_server.py - minimal HP/MP capture server.

Only does these things:
  1. Capture/read the server window HP/MP.
  2. When server HP is missing at least 30 points, ask clients to press F5 and click.

It intentionally does NOT focus the server window, press keys locally, click locally,
move, detect monsters, use F8, or run overlays.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import win32gui
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro

WINDOW_TITLE = "server"
HOST = "0.0.0.0"
PORT = 9997
POLL_INTERVAL_SECONDS = 0.2
STATUS_INTERVAL_SECONDS = 1.0
HEAL_REQUEST_COOLDOWN_SECONDS = 2.5
SERVER_HP_MISSING_TRIGGER = 30

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

MP_READ: dict[str, Any] = {
    "x": 976,
    "y": 96,
    "width": 100,
    "height": 21,
    "color_rgb": (204, 227, 255),
    "x_offsets": [0, 5, 10],
    "text_x_offsets": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "text_y_offsets": [0, 1, 2, 3, 4, 5],
}

_clients: list[dict[str, Any]] = []
_clients_lock = threading.Lock()
_recv_buffers: dict[socket.socket, bytes] = {}
_request_id = 0
_request_id_lock = threading.Lock()
_server_running = True


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
    print("[hp_macro_server] visible windows:")
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

    _print_visible_windows()
    raise RuntimeError(f"window not found: {title!r}")


def _init_target_window(title: str, hwnd: int | None = None) -> None:
    """Set capture target without moving/focusing/renaming the window."""
    target_hwnd = hwnd if hwnd is not None else _find_window(title)
    macro.set_hwnd(target_hwnd)


def _read_stat_text(cfg: dict[str, Any], img: Image.Image) -> str:
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


def read_hp_state(img: Image.Image) -> dict[str, float | int] | None:
    return _parse_stat(_read_stat_text(HP_READ, img))


def read_mp_state(img: Image.Image) -> dict[str, float | int] | None:
    return _parse_stat(_read_stat_text(MP_READ, img))


def _fmt_stat(name: str, state: dict[str, float | int] | None) -> str:
    if state is None:
        return f"{name}=read failed"
    return f"{name}={state['current']}/{state['maximum']} ({state['percent']:.1f}%)"


def read_once() -> None:
    img = macro.screenshot()
    hp_text = _read_stat_text(HP_READ, img)
    mp_text = _read_stat_text(MP_READ, img)
    print(f"[hp_macro_server] HP raw={hp_text!r}  {_fmt_stat('HP', read_hp_state(img))}")
    print(f"[hp_macro_server] MP raw={mp_text!r}  {_fmt_stat('MP', read_mp_state(img))}")


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


def _next_request_id() -> int:
    global _request_id
    with _request_id_lock:
        _request_id += 1
        return _request_id


def _recv_ack(conn: socket.socket, req_id: int, timeout: float) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    old_timeout = conn.gettimeout()
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            conn.settimeout(remaining)
            resp = _recv_json(conn)
            if resp is None:
                return None
            if resp.get("req_id") == req_id:
                return resp
            print(f"[hp_macro_server] ignoring non-matching response: {resp}")
    finally:
        conn.settimeout(old_timeout)


def _remove_client(client: dict[str, Any]) -> None:
    with _clients_lock:
        _clients[:] = [c for c in _clients if c is not client]
    conn = client.get("conn")
    if isinstance(conn, socket.socket):
        try:
            conn.close()
        except OSError:
            pass
        _recv_buffers.pop(conn, None)
    print(f"[hp_macro_server] client removed: {client.get('addr')}")


def _send_command(client: dict[str, Any], payload: dict[str, Any], timeout: float) -> bool:
    conn: socket.socket = client["conn"]
    addr = client["addr"]

    with client["lock"]:
        req_id = _next_request_id()
        msg = dict(payload)
        msg["req_id"] = req_id
        if not _send_json(conn, msg):
            _remove_client(client)
            return False

        ack = _recv_ack(conn, req_id, timeout)
        if ack is None:
            print(f"[hp_macro_server] client timeout/disconnect: {addr}")
            _remove_client(client)
            return False

        ok = ack.get("status") in {"ok", "pong"}
        if not ok:
            print(f"[hp_macro_server] client returned error from {addr}: {ack}")
        return ok


def _broadcast(payload: dict[str, Any], timeout: float = 5.0) -> bool:
    with _clients_lock:
        clients = list(_clients)

    if not clients:
        print(f"[hp_macro_server] no clients for command: {payload}")
        return False

    results = [_send_command(client, payload, timeout) for client in clients]
    return any(results)


def _accept_loop(server_sock: socket.socket) -> None:
    while _server_running:
        try:
            conn, addr = server_sock.accept()
        except OSError:
            break

        client = {"conn": conn, "addr": addr, "lock": threading.Lock()}
        with _clients_lock:
            _clients.append(client)
        print(f"[hp_macro_server] client connected: {addr}")


def run() -> None:
    last_status_time = 0.0
    last_heal_request_time = 0.0

    print(
        "[hp_macro_server] running: capture only, "
        f"request client F5+click when server HP is missing >= {SERVER_HP_MISSING_TRIGGER}"
    )

    while _server_running:
        now = time.time()
        img = macro.screenshot()
        hp_state = read_hp_state(img)
        mp_state = read_mp_state(img)

        if now - last_status_time >= STATUS_INTERVAL_SECONDS:
            print(f"[hp_macro_server] {_fmt_stat('HP', hp_state)}, {_fmt_stat('MP', mp_state)}")
            last_status_time = now

        if hp_state is not None:
            missing_hp = int(hp_state["maximum"]) - int(hp_state["current"])
            heal_needed = missing_hp >= SERVER_HP_MISSING_TRIGGER
            cooldown_ready = now - last_heal_request_time >= HEAL_REQUEST_COOLDOWN_SECONDS
            if heal_needed and cooldown_ready:
                print(
                    f"[hp_macro_server] server HP missing {missing_hp} >= "
                    f"{SERVER_HP_MISSING_TRIGGER} -> request client F5+click"
                )
                if _broadcast({"cmd": "f5_click"}, timeout=5.0):
                    last_heal_request_time = time.time()

        time.sleep(POLL_INTERVAL_SECONDS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal HP/MP capture server.")
    parser.add_argument("--title", default=WINDOW_TITLE, help="Window title to capture. Default: server")
    parser.add_argument("--hwnd", type=int, default=None, help="Explicit window handle to capture.")
    parser.add_argument("--host", default=HOST, help="TCP bind host. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=PORT, help="TCP port. Default: 9997")
    parser.add_argument("--once", action="store_true", help="Read HP/MP once and exit.")
    parser.add_argument("--list-windows", action="store_true", help="List visible windows and exit.")
    return parser.parse_args()


def main() -> int:
    global HOST, PORT, _server_running

    args = parse_args()
    HOST = args.host
    PORT = args.port

    if args.list_windows:
        _print_visible_windows()
        return 0

    _init_target_window(args.title, args.hwnd)

    if args.once:
        read_once()
        return 0

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[hp_macro_server] listening: {HOST}:{PORT}")
    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()

    try:
        run()
    except KeyboardInterrupt:
        print("\n[hp_macro_server] stopping")
    finally:
        _server_running = False
        try:
            server_sock.close()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
