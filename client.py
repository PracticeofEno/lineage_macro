"""
client.py - Pickup 클라이언트
  - 서버에 TCP 연결 후 명령 수신
  - ping 수신 시 readMp()로 마나 측정 후 pong 응답
  - "pickup" 명령 수신 시 pickup_lineage1() 실행
  - "reset_target" 수신 시 target_locked 리셋
  - 소켓 끊김 시 자동 재연결 시도
"""

import socket
import json
import time
import threading
import sys
import win32con
from datetime import datetime, timezone, timedelta

import win32api

import macro

SERVER_HOST = '112.185.118.218'  # ← 서버 IP로 변경
SERVER_PORT = 9999
RECONNECT_DELAY = 5  # 재연결 대기 시간(초)

if len(sys.argv) < 2:
    print("사용법: python client.py <idx>  (예: python client.py 1)")
    sys.exit(1)
CLIENT_IDX = int(sys.argv[1])

running = False
_conn_thread = None
_recv_buffers: dict[socket.socket, bytes] = {}


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


def _handle_command(msg: dict) -> dict | None:
    cmd = msg.get("cmd")
    req_id = msg.get("req_id")

    if cmd == "ping":
        mp = macro.readMp()
        print(f"[client] ping 수신 → MP: {mp}")
        return {"status": "pong", "mp": mp, "req_id": req_id}

    if cmd == "click":
        x, y = msg.get("x", 1080), msg.get("y", 174)
        print(f"[client] 클릭 명령 수신: ({x}, {y})")
        macro.force_set_foreground_window(macro.lineage1_hwnd)
        win32api.SetCursorPos((1080, 174))
        time.sleep(1)
        macro.arduino_mouse_click_left()
        time.sleep(2)
        macro.arduino_key_press(win32con.VK_F10)
        time.sleep(1)
        win32api.SetCursorPos((105, 85))
        time.sleep(1)
        macro.arduino_mouse_click_left()
        macro.arduino_mouse_click_left()
        return {"status": "ok", "req_id": req_id}

    if cmd == "pickup":
        target = msg.get("target")
        nickname = msg.get("nickname")
        recv_time = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
        print(f"[client] 픽업 명령 수신: {target} ({recv_time})")
        macro.pickup_lineage1(target_nickname=nickname)
        return {"status": "ok", "req_id": req_id}

    if cmd == "potion":
        print(f"[client] 포션 명령 수신")
        macro.use_potion()
        return {"status": "ok", "req_id": req_id}

    if cmd == "move_coord":
        dx, dy = msg.get("dx", 0), msg.get("dy", 0)
        macro.apply_coord_delta(dx, dy)
        print(f"[client] 좌표 이동 수신: dx={dx:+}, dy={dy:+}")
        return None

    if cmd == "reset_coord":
        macro.reset_coord()
        return None

    print(f"[client] 알 수 없는 명령: {msg}")
    return None


def _run(conn: socket.socket):
    print("[client] 서버 연결됨")
    while running:
        msg = _recv_json(conn)
        if msg is None:
            print("[client] 서버 연결 끊김")
            break

        resp = _handle_command(msg)
        if resp is not None:
            if not _send_json(conn, resp):
                print("[client] 응답 전송 실패")
                break


def _connect_loop():
    while running:
        conn = None
        try:
            print(f"[client] 서버에 연결 시도 중: {SERVER_HOST}:{SERVER_PORT}")
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((SERVER_HOST, SERVER_PORT))
            _send_json(conn, {"cmd": "register", "idx": CLIENT_IDX})
            _run(conn)
        except (ConnectionRefusedError, OSError) as e:
            print(f"[client] 연결 실패: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass
                _recv_buffers.pop(conn, None)

        if running:
            print(f"[client] {RECONNECT_DELAY}초 후 재연결...")
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    macro.init_setting("client")

    print("명령어: 1=연결 시작, 2=연결 중지, q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            break
        elif cmd == "1":
            if _conn_thread is None or not _conn_thread.is_alive():
                running = True
                _conn_thread = threading.Thread(target=_connect_loop, daemon=True)
                _conn_thread.start()
                print("[client] 연결 시작됨")
            else:
                print("[client] 이미 실행 중")
        elif cmd == "2":
            running = False
            print("[client] 연결 중지됨")
