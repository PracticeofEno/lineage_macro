"""
client.py - Pickup 클라이언트
  - 서버에 TCP 연결 후 명령 수신
  - ping 수신 시 readMp()로 마나 측정 후 pong 응답
  - "pickup" 명령 수신 시 pickup_lineage1() 실행
  - "reset_target" 수신 시 target_locked 리셋
  - 소켓 끊김 시 자동 재연결 시도
"""

import socket
import os
import atexit
import json
import time
import threading
import sys
from datetime import datetime, timezone, timedelta

import macro

SERVER_HOST = '172.30.1.70'  # ← 서버 IP로 변경
SERVER_PORT = 9999
RECONNECT_DELAY = 5  # 재연결 대기 시간(초)

if len(sys.argv) < 2:
    print("사용법: python client.py <idx>  (예: python client.py 1)")
    sys.exit(1)
CLIENT_IDX = int(sys.argv[1])

running = False
_conn_thread = None
_window_slot = None
_window_slot_lock_fd = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()


def _slot_lock_path(slot_num: int) -> str:
    return os.path.join(
        _BASE_DIR,
        f".client_slot_{CLIENT_IDX}_{slot_num}.lock",
    )


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_window_slot() -> tuple[str, str]:
    global _window_slot
    global _window_slot_lock_fd

    pid = os.getpid()
    slot_num = 1

    while True:
        lock_path = _slot_lock_path(slot_num)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(pid).encode())
            _window_slot = slot_num
            _window_slot_lock_fd = fd
            window_title = "client" if slot_num == 1 else f"client{slot_num}"
            window_role = "client" if slot_num == 1 else "client_numbering"
            print(f"[client] 로컬 창 슬롯 할당: {window_title} (pc idx={CLIENT_IDX})")
            return window_title, window_role
        except FileExistsError:
            try:
                with open(lock_path, encoding="utf-8") as f:
                    existing_pid = int(f.read().strip())
            except (OSError, ValueError):
                existing_pid = -1

            if not _is_pid_alive(existing_pid):
                try:
                    os.remove(lock_path)
                    continue
                except OSError:
                    pass

            slot_num += 1


def _release_window_slot() -> None:
    global _window_slot
    global _window_slot_lock_fd

    if _window_slot is None:
        return

    if _window_slot_lock_fd is not None:
        try:
            os.close(_window_slot_lock_fd)
        except OSError:
            pass
        _window_slot_lock_fd = None

    try:
        os.remove(_slot_lock_path(_window_slot))
    except OSError:
        pass

    _window_slot = None


atexit.register(_release_window_slot)


def _send_json(conn: socket.socket, obj: dict) -> bool:
    try:
        conn.sendall((json.dumps(obj) + '\n').encode())
        return True
    except OSError:
        return False


def _recv_json(conn: socket.socket) -> dict | None:
    buf = b''
    try:
        while b'\n' not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        return json.loads(buf.split(b'\n')[0].decode())
    except (OSError, json.JSONDecodeError):
        return None


def _handle_command(msg: dict) -> dict | None:
    cmd = msg.get("cmd")

    if cmd == "ping":
        mp = macro.readMp()
        print(f"[client] ping 수신 → MP: {mp}")
        return {"status": "pong", "mp": mp}

    if cmd == "pickup":
        target = msg.get("target")
        nickname = msg.get("nickname")
        recv_time = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
        print(f"[client] 픽업 명령 수신: {target} ({recv_time})")
        macro.pickup_lineage1(target_nickname=nickname)
        return {"status": "ok"}

    if cmd == "potion":
        print(f"[client] 포션 명령 수신")
        macro.use_potion()
        return {"status": "ok"}

    if cmd == "reset_target":
        macro.target_locked = False
        print("[client] 타겟 리셋")
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

        if running:
            print(f"[client] {RECONNECT_DELAY}초 후 재연결...")
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    window_title, window_role = _acquire_window_slot()
    macro.init_custom_hwnd(window_title, window_role)

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

