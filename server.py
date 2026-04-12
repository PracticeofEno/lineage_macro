"""
server.py - Exchange 서버
  - TCP 소켓으로 클라이언트 연결 관리
  - accept_exchange_and_track_adena 로직 수행
  - pickup 시 연결된 클라이언트에게 명령 전송 후 ack 대기
"""

import socket
import threading
import json
import time
import win32api
import win32con
import win32gui

import macro
import imageProcesser
import ocr

HOST = '0.0.0.0'
PORT = 9999
ACK_TIMEOUT = 10  # 픽업 ack 대기 최대 시간(초)

# ── 클라이언트 관리 ───────────────────────────────────────────────────────────
_clients: list[tuple[socket.socket, tuple]] = []
_clients_lock = threading.Lock()

running = True          # exchange 루프 제어 (cmd 1=시작, 2=중지)
_server_running = True  # accept 루프 제어 (q 입력 시에만 False)


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


def _remove_client(conn: socket.socket, addr: tuple):
    with _clients_lock:
        _clients[:] = [(c, a) for c, a in _clients if c is not conn]
    try:
        conn.close()
    except OSError:
        pass
    print(f"[server] 클라이언트 제거됨: {addr}")


def _handle_client(conn: socket.socket, addr: tuple):
    print(f"[server] 클라이언트 연결: {addr}")
    with _clients_lock:
        _clients.append((conn, addr))
    # 클라이언트가 연결을 끊을 때까지 유지 (명령은 메인 스레드에서 전송)
    try:
        while True:
            # heartbeat: 5초마다 ping
            time.sleep(5)
            if not _send_json(conn, {"cmd": "ping"}):
                break
    finally:
        _remove_client(conn, addr)


def _accept_loop(server_sock: socket.socket):
    while _server_running:
        try:
            conn, addr = server_sock.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
        except OSError:
            break


# ── 픽업 명령 전송 ─────────────────────────────────────────────────────────────
def _send_pickup(target: str) -> bool:
    """
    연결된 클라이언트 중 첫 번째에게 pickup 명령을 보내고 ack를 기다린다.
    클라이언트가 없으면 False 반환.
    """
    with _clients_lock:
        if not _clients:
            print("[server] 연결된 클라이언트 없음 - 픽업 스킵")
            return False
        conn, addr = _clients[0]

    if not _send_json(conn, {"cmd": "pickup", "target": target}):
        _remove_client(conn, addr)
        return False

    # ack 수신 (timeout 적용)
    conn.settimeout(ACK_TIMEOUT)
    resp = _recv_json(conn)
    conn.settimeout(None)

    if resp is None:
        print(f"[server] ack 수신 실패 - 클라이언트 제거: {addr}")
        _remove_client(conn, addr)
        return False

    if resp.get("status") == "ok":
        print(f"[server] 픽업 완료 ack 수신 ({target}) from {addr}")
        return True

    print(f"[server] 예상치 못한 응답: {resp}")
    return False


# ── Exchange 루프 (accept_exchange_and_track_adena 변형) ──────────────────────
def exchange_loop():
    global running

    WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)
    stage = WAIT_NICKNAME

    greeted_nickname = None
    adena_before = None
    prev_brightness = None
    brightness_changed = False
    _last_type_string_time = 0

    while running:
        # ── Stage 1: MP 읽기 / 방향 조정 / 광고 / 닉네임 대기 ──────────────
        if stage == WAIT_NICKNAME:
            img = macro.screenshot(hwnd=macro.lineage1_hwnd)
            _mp1 = macro.readMp(img)
            if _mp1 != 0:
                macro.mp_1 = _mp1
            macro.available_count_1 = int(macro.mp_1 // 20)
            total_count = macro.available_count_1
            print(total_count, macro.available_count_1, macro.mp_1, macro.direction_threshold)

            if total_count < macro.direction_threshold:
                if macro.current_direction != macro.low_count_direction:
                    macro.force_set_foreground_window(macro.lineage1_hwnd)
                    macro._DIRECTION_FUNCS[macro.low_count_direction]()
                    time.sleep(1)
                time.sleep(0.5)
                continue
            else:
                if macro.current_direction != macro.high_count_direction:
                    macro.force_set_foreground_window(macro.lineage1_hwnd)
                    time.sleep(1)
                    macro._DIRECTION_FUNCS[macro.high_count_direction]()
                    time.sleep(1)

            if time.time() - _last_type_string_time >= 5:
                macro.arduino_type_string(
                    f"\\f2 방당 {macro.adena_per_pickup} \\f= {total_count}방 가능"
                )
                _last_type_string_time = time.time()

            nickname = macro.readExchangeNickname(macro.screenshot())
            if nickname:
                greeted_nickname = nickname
                macro.arduino_type_string(f"최대 {total_count}방 입니다! 확인!")
                stage = READ_ADENA
                continue

            macro._arduino_send(f'KP,{win32con.VK_F7}')
            time.sleep(0.5)

        # ── Stage 2: 교환 전 아데나 1회 측정 ────────────────────────────────
        elif stage == READ_ADENA:
            adena_before = macro.readAdena()
            stage = MONITOR_BRIGHTNESS

        # ── Stage 3: 슬롯 밝기 감시 → 변화 시 교환 수락 ────────────────────
        elif stage == MONITOR_BRIGHTNESS:
            img = macro.screenshot()
            if not macro.readExchangeNickname(img):
                stage = PICKUP
                continue

            slot = imageProcesser.crop(img, 241, 360, 30, 30)
            brightness = macro.get_brightness(slot)
            print(f"[server] 슬롯 밝기: {brightness:.2f}")

            if prev_brightness is not None and brightness != prev_brightness:
                brightness_changed = True
                win32api.SetCursorPos((248, 585))
                time.sleep(0.5)
                macro._arduino_send('CL')
                time.sleep(0.5)
                macro.key_press(ord('Y'))
                time.sleep(0.1)
                macro._arduino_send(f'KP,{win32con.VK_RETURN}')

            prev_brightness = brightness
            time.sleep(0.5)

        # ── Stage 4: 받은 아데나 계산 → 클라이언트에 픽업 명령 ──────────────
        elif stage == PICKUP:
            if not brightness_changed:
                # 교환 없이 닉네임만 사라진 경우
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

            adena_after = macro.readAdena()
            received = adena_after - adena_before
            print(f"[server] 교환 완료: {adena_before} -> {adena_after} (+{received})")

            pickup_count = int(received // macro.adena_per_pickup)
            print(f"[server] 픽업 횟수: {pickup_count}")

            # 활성 윈도우가 server가 아니라면 포그라운드로 전환해야함
            if win32gui.GetForegroundWindow() != macro.lineage1_hwnd:
                macro.force_set_foreground_window(macro.lineage1_hwnd)
            time.sleep(0.5)
            # macro.arduino_type_string(f"{greeted_nickname}님 고맙습니다~!")

            # 상태 초기화 후 다음 교환 대기
            stage = WAIT_NICKNAME
            greeted_nickname = None
            adena_before = None
            prev_brightness = None
            brightness_changed = False


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    macro.init_lineage_windows("server")
    macro.init_mouse_x_y()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[server] 대기 중: {HOST}:{PORT}")

    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()

    print("\n명령어: q=종료, 1=exchange 시작, 2=exchange 중지")
    exchange_thread = None
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            _server_running = False
            server_sock.close()
            break
        if cmd == "1":
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            running = True
            exchange_thread = threading.Thread(target=exchange_loop, daemon=True)
            exchange_thread.start()
        if cmd == "2":
            running = False
