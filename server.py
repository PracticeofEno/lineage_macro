"""
server.py - Exchange 서버
  - TCP 소켓으로 클라이언트 연결 관리
  - ping-pong 시 각 client MP를 수신하여 개별 저장
  - pickup 시 서버/클라이언트 픽업 분배
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

HOST = '0.0.0.0'
PORT = 9999
ACK_TIMEOUT = 10      # 픽업 ack 대기 최대 시간(초)
SAME_UNIT_DELAY = 2   # 같은 PC 내 클라이언트 간 픽업 딜레이(초)

# ── 클라이언트 관리 ───────────────────────────────────────────────────────────
# client: {"conn": socket, "addr": tuple, "lock": Lock, "mp": int, "idx": int}
# idx  : 클라이언트 실행 시 인자로 지정 (0=서버와 같은 PC, 같은 PC끼리 동일 idx 사용)
# lock : ping-pong과 pickup 명령이 같은 소켓을 동시에 사용하지 않도록 보호
_clients: list[dict] = []
_clients_lock = threading.Lock()

def _group_key(idx: int) -> int:
    """같은 idx는 같은 PC 그룹. 서버(-1)는 idx=0과 동일 그룹으로 정규화"""
    return 0 if idx == -1 else idx

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


def _remove_client(client: dict):
    with _clients_lock:
        _clients[:] = [e for e in _clients if e is not client]
    try:
        client["conn"].close()
    except OSError:
        pass
    print(f"[server] 클라이언트 제거됨: {client['addr']}")


def _handle_client(conn: socket.socket, addr: tuple):
    # 첫 메시지로 클라이언트가 보낸 idx 수신
    conn.settimeout(10)
    reg = _recv_json(conn)
    conn.settimeout(None)
    if reg is None or reg.get("cmd") != "register":
        print(f"[server] 등록 실패 (잘못된 메시지): {addr}")
        conn.close()
        return
    idx = reg.get("idx")
    if not isinstance(idx, int):
        print(f"[server] 등록 실패 (idx 없음): {addr}")
        conn.close()
        return

    client = {"conn": conn, "addr": addr, "lock": threading.Lock(), "mp": 0, "idx": idx, "available": 0}
    unit_str = "서버PC" if idx == 0 else f"유닛{idx}"
    print(f"[server] 클라이언트 등록: {addr}  (idx={idx}, {unit_str})")
    with _clients_lock:
        _clients.append(client)
    try:
        while True:
            time.sleep(5)
            with client["lock"]:
                if not _send_json(conn, {"cmd": "ping"}):
                    break
                conn.settimeout(10)
                resp = _recv_json(conn)
                conn.settimeout(None)
                if resp is None:
                    break
                if resp.get("status") == "pong":
                    client["mp"] = resp.get("mp", 0)
                    client["available"] = int(client["mp"] // 20)
                    print(f"[server] client {addr} MP: {client['mp']}  available: {client['available']}")
    finally:
        _remove_client(client)


def _accept_loop(server_sock: socket.socket):
    while _server_running:
        try:
            conn, addr = server_sock.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
        except OSError:
            break


# ── 픽업 명령 전송 ─────────────────────────────────────────────────────────────
def _send_pickup(client: dict) -> bool:
    """특정 클라이언트에게 pickup 명령을 보내고 ack를 기다린다."""
    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        if not _send_json(conn, {"cmd": "pickup", "target": "lineage1"}):
            _remove_client(client)
            return False

        conn.settimeout(ACK_TIMEOUT)
        resp = _recv_json(conn)
        conn.settimeout(None)

        if resp is None:
            print(f"[server] ack 수신 실패 - 클라이언트 제거: {addr}")
            _remove_client(client)
            return False

        if resp.get("status") == "ok":
            print(f"[server] 픽업 완료 ack 수신 from {addr}")
            return True

        print(f"[server] 예상치 못한 응답: {resp}")
        return False


# ── Exchange 루프 ──────────────────────────────────────────────────────────────
def exchange_loop():
    global running

    WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)
    stage = WAIT_NICKNAME

    greeted_nickname = None
    adena_before = None
    prev_brightness = None
    brightness_changed = False
    _last_type_string_time = 0
    clients_snapshot = []

    while running:
        # ── Stage 1: MP 읽기 / 방향 조정 / 광고 / 닉네임 대기 ──────────────
        if stage == WAIT_NICKNAME:
            img = macro.screenshot(hwnd=macro.lineage1_hwnd)
            _mp1 = macro.readMp(img)
            if _mp1 != 0:
                macro.mp_1 = _mp1

            with _clients_lock:
                for e in _clients:
                    if e["idx"] == -1:
                        e["mp"] = macro.mp_1
                        e["available"] = int(macro.mp_1 // 20)
                        break
                clients_snapshot = list(_clients)

            total_count = sum(e["available"] for e in clients_snapshot)
            print(f"total={total_count}  threshold={macro.direction_threshold}")
            for e in clients_snapshot:
                label = "server" if e["idx"] == -1 else f"client {e['addr']}"
                print(f"  {label}: {e['available']}회({e['mp']}MP)")

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

            # if time.time() - _last_type_string_time >= 5:
            #     macro.arduino_type_string(
            #         f"\\f2 방당 {macro.adena_per_pickup} \\f= {total_count}방 가능"
            #     )
            #     _last_type_string_time = time.time()

            nickname = macro.readExchangeNickname(macro.screenshot())
            if nickname:
                greeted_nickname = nickname
                # macro.arduino_type_string(f"최대 {total_count}방 입니다! 확인!")
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

        # ── Stage 4: 받은 아데나 계산 → 서버/클라이언트 픽업 분배 ──────────
        elif stage == PICKUP:
            if not brightness_changed:
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

            remaining = pickup_count
            # ── 픽업 분배 ───────────────────────────────────────────────────
            # 매 라운드: 그룹별 available 최고 대표 선출 → idx 내림차순 전송
            # 같은 그룹(-1/0)은 SAME_UNIT_DELAY 이내 재전송 금지
            last_group_time: dict = {}

            while remaining > 0:
                # 그룹별 available 최고 클라이언트 선출
                groups: dict = {}
                for c in clients_snapshot:
                    gk = _group_key(c["idx"])
                    if gk not in groups or c["available"] > groups[gk]["available"]:
                        groups[gk] = c

                if not groups:
                    break

                # idx 내림차순 정렬
                ordered = sorted(groups.values(), key=lambda c: c["idx"], reverse=True)

                sent_any = False
                for c in ordered:
                    if remaining <= 0:
                        break
                    gk = _group_key(c["idx"])
                    elapsed = time.time() - last_group_time.get(gk, 0)
                    if elapsed < SAME_UNIT_DELAY:
                        time.sleep(SAME_UNIT_DELAY - elapsed)

                    label = "server" if c["idx"] == -1 else f"client{c['addr']}"
                    print(f"[server] pickup → {label} (remaining={remaining})")

                    if c["idx"] == -1:
                        macro.pickup()
                        ok = True
                    else:
                        ok = _send_pickup(c)

                    last_group_time[gk] = time.time()
                    if ok:
                        remaining -= 1
                        sent_any = True

                if not sent_any:
                    break

            if win32gui.GetForegroundWindow() != macro.lineage1_hwnd:
                macro.force_set_foreground_window(macro.lineage1_hwnd)
            time.sleep(0.5)
            # macro.arduino_type_string(f"{greeted_nickname}님 고맙습니다~!")

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

    # 서버 자신을 idx=-1 로 _clients에 등록 (conn/addr/lock 없음)
    with _clients_lock:
        _clients.append({"idx": -1, "mp": 0, "available": 0})

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
