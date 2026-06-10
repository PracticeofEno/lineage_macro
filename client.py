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
PICKUP_MP_VERIFY_DELAY_SECONDS = 0.8
PICKUP_MP_RETRY_DELAY_SECONDS = 0.3

if len(sys.argv) < 2:
    print("사용법: python client.py <idx>  (예: python client.py 1)")
    sys.exit(1)
CLIENT_IDX = int(sys.argv[1])

running = False
_conn_thread = None
_restart_watcher_stop_reported = False


def _handle_restart_watcher_click() -> None:
    global _restart_watcher_stop_reported
    if not _restart_watcher_stop_reported:
        print(f"[client idx({CLIENT_IDX})] Restart watcher clicked - connection kept")
        _restart_watcher_stop_reported = True


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


def _read_mp_for_pickup(logs: list[str], label: str) -> tuple[int | None, bool]:
    try:
        mp = macro.readMp()
    except macro.RestartButtonClicked:
        _handle_restart_watcher_click()
        logs.append(f"{label} MP 읽기 중 Restart 감지")
        return None, True

    if mp is None:
        logs.append(f"{label} MP 읽기 실패")
    else:
        logs.append(f"{label} MP={mp}")
    return mp, False


def _read_expected_mp(raw) -> int | None:
    try:
        if raw is None:
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _run_pickup_once(
    *,
    target_nickname: str | None,
    direction: str | None,
    logs: list[str],
    attempt: int,
) -> tuple[str, int | None]:
    logs.append(f"헤이스트 시도 {attempt}/2")
    ok = macro.pickup_lineage1(
        target_nickname=target_nickname,
        direction=direction,
        log_messages=logs,
        print_logs=False,
        log_prefix="",
    )
    if not ok:
        return "target_failed", None

    time.sleep(PICKUP_MP_VERIFY_DELAY_SECONDS)
    after_mp, restart_detected = _read_mp_for_pickup(logs, f"시도 {attempt} 후")
    if restart_detected:
        return "restart_detected", None
    if after_mp is None:
        return "mp_read_failed", None
    return "attempted", after_mp


def _handle_command(msg: dict) -> dict | None:
    global running, _restart_watcher_stop_reported
    cmd = msg.get("cmd")

    if cmd == "ping":
        logs = []
        try:
            mp = macro.readMp()
        except macro.RestartButtonClicked:
            _restart_watcher_stop_reported = True
            return {"status": "restart_detected", "mp": None, "logs": ["Restart clicked - connection kept"]}
        if mp is None:
            logs.append("MP 읽기 실패")
            return {"status": "pong", "mp": mp, "logs": logs}
        resp = {"status": "pong", "mp": mp}
        if logs:
            resp["logs"] = logs
        return resp

    if cmd == "pickup":
        target = msg.get("target")
        nickname = msg.get("nickname")
        direction = msg.get("direction")
        recv_time = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
        logs = [f"픽업 명령 수신 - target={target}, time={recv_time}"]
        reference_mp = _read_expected_mp(msg.get("expected_mp"))
        if reference_mp is None:
            before_mp, restart_detected = _read_mp_for_pickup(logs, "시도 전")
            if restart_detected:
                return {"status": "restart_detected", "mp": None, "logs": logs}
            reference_mp = before_mp
        else:
            logs.append(f"서버 MP 기준 사용 - before={reference_mp}")

        if reference_mp is None:
            logs.append("MP 기준값 없음 - 다른 캐릭터로 넘김")
            return {"status": "mp_read_failed", "mp": None, "logs": logs}

        status, after_mp = _run_pickup_once(
            target_nickname=nickname,
            direction=direction,
            logs=logs,
            attempt=1,
        )
        if status != "attempted":
            return {"status": status, "mp": after_mp, "logs": logs}
        if after_mp < reference_mp:
            logs.append(f"MP 소모 확인 - before={reference_mp}, after={after_mp}")
            return {"status": "ok", "mp": after_mp, "logs": logs}

        logs.append(f"MP 소모 미확인 - before={reference_mp}, after={after_mp}, retry")
        time.sleep(PICKUP_MP_RETRY_DELAY_SECONDS)
        retry_reference_mp = after_mp
        status, retry_after_mp = _run_pickup_once(
            target_nickname=nickname,
            direction=direction,
            logs=logs,
            attempt=2,
        )
        if status == "target_failed":
            return {"status": "target_failed", "mp": retry_after_mp, "logs": logs}
        if status == "restart_detected":
            return {"status": "restart_detected", "mp": retry_after_mp, "logs": logs}
        if status == "mp_read_failed":
            logs.append("재시도 후 MP 읽기 실패 - 다른 캐릭터로 넘김")
            return {"status": "mp_read_failed", "mp": retry_after_mp, "logs": logs}
        if retry_after_mp is not None and retry_after_mp < retry_reference_mp:
            logs.append(f"재시도 MP 소모 확인 - before={retry_reference_mp}, after={retry_after_mp}")
            return {"status": "ok", "mp": retry_after_mp, "logs": logs}

        logs.append(f"재시도 후에도 MP 소모 없음 - before={retry_reference_mp}, after={retry_after_mp}")
        return {"status": "mp_not_spent", "mp": retry_after_mp, "logs": logs}

    if cmd == "potion":
        logs = ["포션 명령 수신"]
        macro.use_potion()
        return {"status": "ok", "logs": logs}

    if cmd == "restart":
        logs = ["Restart 명령 수신"]
        clicked = macro.click_restart_if_visible()
        _restart_watcher_stop_reported = True
        if clicked:
            logs.append("Restart clicked - connection kept")
        else:
            logs.append("Restart not visible - connection kept")
        return {"status": "ok", "clicked": clicked, "logs": logs}

    if cmd == "chat":
        message = str(msg.get("message", "")).strip()
        if not message:
            return {"status": "ok"}
        logs = [f"채팅 명령 수신 - message={message}"]
        if not getattr(macro, "CHAT_INPUT_ENABLED", True):
            logs.append("채팅 입력 비활성화 - skipped")
            return {"status": "ok", "logs": logs}
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
    macro.start_restart_watcher(on_click=_handle_restart_watcher_click)

    print("명령어: 1=연결 시작, 2=연결 중지, q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            break
        elif cmd == "1":
            if _conn_thread is None or not _conn_thread.is_alive():
                running = True
                _restart_watcher_stop_reported = False
                _conn_thread = threading.Thread(target=_connect_loop, daemon=True)
                _conn_thread.start()
                print(f"[client idx({CLIENT_IDX})] 연결 시작")
            else:
                print(f"[client idx({CLIENT_IDX})] 이미 실행 중")
        elif cmd == "2":
            running = False
            print(f"[client idx({CLIENT_IDX})] 연결 중지")
