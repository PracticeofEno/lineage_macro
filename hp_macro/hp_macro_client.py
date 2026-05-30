"""
hp_macro_client.py - HP/MP 액션 클라이언트
  - 서버로부터 명령 수신 후 실행
  - hold_f5: F5 홀드 → 좌클릭
  - press_f8: F8 입력
"""

import argparse
import json
import socket
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro
import win32api
import win32con
import win32gui

SERVER_HOST = '112.185.118.218'
SERVER_PORT = 9997
RECONNECT_DELAY = 5
WINDOW_TITLE = "client"
CLICK_INTERVAL = 0.5

_recv_buffers: dict[socket.socket, bytes] = {}
running = False
_hold_f5_active = False
_conn_thread = None
_current_conn: socket.socket | None = None


def _send_json(conn: socket.socket, obj: dict) -> bool:
    try:
        conn.sendall((json.dumps(obj) + '\n').encode())
        return True
    except OSError:
        return False


def _recv_json(conn: socket.socket) -> dict | None:
    buf = _recv_buffers.pop(conn, b'')
    try:
        while b'\n' not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        line, rest = buf.split(b'\n', 1)
        if rest:
            _recv_buffers[conn] = rest
        return json.loads(line.decode())
    except (OSError, json.JSONDecodeError):
        return None


def _find_window(title: str) -> int:
    windows: list[tuple[str, int]] = []

    def callback(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd)
        if t:
            windows.append((t, hwnd))

    win32gui.EnumWindows(callback, None)
    for t, hwnd in windows:
        if t.startswith(title):
            return hwnd
    raise RuntimeError(f"window not found: {title}")


def _hold_f5_and_click(seconds: float) -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F5)
    macro.arduino_mouse_click_left()


def _press_f8() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F8)


def _handle_command(msg: dict) -> dict | None:
    global _hold_f5_active, running
    cmd = msg.get("cmd")
    req_id = msg.get("req_id")

    if cmd == "ping":
        return {"status": "pong", "req_id": req_id}

    if cmd == "start":
        _hold_f5_active = True
        print("[hp_macro_client] hold_f5 루프 시작")
        return {"status": "ok", "req_id": req_id}

    if cmd == "stop":
        running = False
        _hold_f5_active = False
        print("[hp_macro_client] hold_f5 루프 정지")
        return {"status": "ok", "req_id": req_id}

    if cmd == "hold_f5":
        _hold_f5_active = True
        return {"status": "ok", "req_id": req_id}

    if cmd == "press_f8":
        print("[hp_macro_client] press_f8")
        _press_f8()
        return {"status": "ok", "req_id": req_id}

    print(f"[hp_macro_client] 알 수 없는 명령: {msg}")
    return None


def _run(conn: socket.socket):
    global _current_conn
    _current_conn = conn
    print("[hp_macro_client] 서버 연결됨")
    while running:
        msg = _recv_json(conn)
        if msg is None:
            print("[hp_macro_client] 서버 연결 끊김")
            break
        resp = _handle_command(msg)
        if resp is not None:
            if not _send_json(conn, resp):
                print("[hp_macro_client] 응답 전송 실패")
                break
    _current_conn = None


def _connect_loop():
    while running:
        conn = None
        try:
            print(f"[hp_macro_client] 서버 연결 시도: {SERVER_HOST}:{SERVER_PORT}")
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((SERVER_HOST, SERVER_PORT))
            _run(conn)
        except (ConnectionRefusedError, OSError) as e:
            print(f"[hp_macro_client] 연결 실패: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass
                _recv_buffers.pop(conn, None)
        if running:
            print(f"[hp_macro_client] {RECONNECT_DELAY}초 후 재연결...")
            time.sleep(RECONNECT_DELAY)

def main() -> int:
    global SERVER_HOST, SERVER_PORT, running, _conn_thread

    macro.set_hwnd(_find_window("client"))

    print("명령어: 1=연결 시작, 2=연결 중지, q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            break
        elif cmd == "1":
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            time.sleep(0.5)
            win32api.SetCursorPos((605,360))
            time.sleep(0.5)
            macro.arduino_mouse_click_left()
            time.sleep(0.5)
            if _conn_thread is None or not _conn_thread.is_alive():
                running = True
                _conn_thread = threading.Thread(target=_connect_loop, daemon=True)
                _conn_thread.start()
                print("[hp_macro_client] 연결 시작됨")
                while running:
                    if _hold_f5_active:
                        _hold_f5_and_click(0)
                        if _current_conn:
                            _recv_buffers.pop(_current_conn, None)
                    time.sleep(CLICK_INTERVAL)
                    macro.arduino_mouse_click_left()
                    time.sleep(0.1)
                    
            else:
                print("[hp_macro_client] 이미 실행 중")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
