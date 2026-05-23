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
from datetime import datetime, timezone, timedelta

import macro

SERVER_HOST = '127.0.0.1'  # ← 서버 IP로 변경
# SERVER_HOST = '192.168.35.63' # DELL
# SERVER_HOST = '192.168.35.55' # ACER
SERVER_PORT = 9999
CHAT_FOCUS_SETTLE_SECONDS = 0.25
CHAT_SEND_SETTLE_SECONDS = 0.8
RECONNECT_DELAY = 5  # 재연결 대기 시간(초)

if len(sys.argv) < 2:
    print("사용법: python client.py <idx>  (예: python client.py 1)")
    sys.exit(1)
CLIENT_IDX = int(sys.argv[1])

running = False
_conn_thread = None


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
    global running
    cmd = msg.get("cmd")

    if cmd == "ping":
        try:
            mp = macro.readMp()
        except macro.RestartButtonClicked:
            running = False
            return {"status": "stopped", "mp": None, "logs": ["Restart clicked - client macro stopped"]}
        if mp is None:
            macro.press_ctrl_a_for_mp_retry(print_log=False)
            return {"status": "pong", "mp": mp, "logs": ["MP 읽기 실패 - action=ctrl_a"]}
        return {"status": "pong", "mp": mp}

    if cmd == "pickup":
        target = msg.get("target")
        nickname = msg.get("nickname")
        direction = msg.get("direction")
        recv_time = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
        logs = [f"픽업 명령 수신 - target={target}, time={recv_time}"]
        ok = macro.pickup_lineage1(
            target_nickname=nickname,
            direction=direction,
            log_messages=logs,
            print_logs=False,
            log_prefix="",
        )
        if not ok:
            return {"status": "target_failed", "logs": logs}
        return {"status": "ok", "logs": logs}

    if cmd == "potion":
        logs = ["포션 명령 수신"]
        macro.use_potion()
        return {"status": "ok", "logs": logs}

    if cmd == "chat":
        message = str(msg.get("message", "")).strip()
        if not message:
            return {"status": "ok"}
        logs = [f"채팅 명령 수신 - message={message}"]
        macro.force_set_foreground_window(macro.lineage1_hwnd)
        time.sleep(CHAT_FOCUS_SETTLE_SECONDS)
        macro.arduino_type_string(message)
        time.sleep(CHAT_SEND_SETTLE_SECONDS)
        logs.append(
            "chat input settled - "
            f"focus_delay={CHAT_FOCUS_SETTLE_SECONDS}, send_delay={CHAT_SEND_SETTLE_SECONDS}"
        )
        return {"status": "ok", "logs": logs}

    print(f"[client idx({CLIENT_IDX})] 알 수 없는 명령 - msg={msg}")
    return None


def _run(conn: socket.socket):
    print(f"[client idx({CLIENT_IDX})] 서버 연결됨")
    while running:
        msg = _recv_json(conn)
        if msg is None:
            print(f"[client idx({CLIENT_IDX})] 서버 연결 끊김")
            break

        resp = _handle_command(msg)
        if resp is not None:
            if not _send_json(conn, resp):
                print(f"[client idx({CLIENT_IDX})] 응답 전송 실패")
                break


def _connect_loop():
    while running:
        conn = None
        try:
            print(f"[client idx({CLIENT_IDX})] 서버 연결 시도 - host={SERVER_HOST}, port={SERVER_PORT}")
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.connect((SERVER_HOST, SERVER_PORT))
            _send_json(conn, {"cmd": "register", "idx": CLIENT_IDX})
            _run(conn)
        except (ConnectionRefusedError, OSError) as e:
            print(f"[client idx({CLIENT_IDX})] 서버 연결 실패 - error={e}")
        finally:
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass

        if running:
            print(f"[client idx({CLIENT_IDX})] 서버 재연결 대기 - seconds={RECONNECT_DELAY}")
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
                print(f"[client idx({CLIENT_IDX})] 연결 시작")
            else:
                print(f"[client idx({CLIENT_IDX})] 이미 실행 중")
        elif cmd == "2":
            running = False
            print(f"[client idx({CLIENT_IDX})] 연결 중지")
