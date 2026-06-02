"""
server.py - Exchange 서버
  - TCP 소켓으로 클라이언트 연결 관리
  - ping-pong 시 각 client MP를 수신하여 개별 저장
  - pickup 시 서버/클라이언트 픽업 분배
"""

import os
import socket
import threading
import json
import time
import random
import win32api
import win32con
import win32gui

import macro

HOST = '0.0.0.0'
PORT = 9999
ACK_TIMEOUT = 10      # 픽업 ack 대기 최대 시간(초)
SAME_UNIT_DELAY = 1   # 같은 PC 내 클라이언트 간 픽업 딜레이(초)
POTION_COOLDOWN = 600 # 포션 쿨타임(초)
HEAL_MP_COST = 5      # 힐 1회 시전에 필요한 MP. 이 값 미만이면 힐 대신 포션(F6) 사용

# ── 클라이언트 관리 ───────────────────────────────────────────────────────────
# client: {"conn": socket, "addr": tuple, "lock": Lock, "mp": int, "idx": int}
# idx  : 클라이언트 실행 시 인자로 지정 (0=서버와 같은 PC, 같은 PC끼리 동일 idx 사용)
# lock : ping-pong과 pickup 명령이 같은 소켓을 동시에 사용하지 않도록 보호
_clients: list[dict] = []
_clients_lock = threading.Lock()
_recv_buffers: dict[socket.socket, bytes] = {}
_request_id = 0
_request_id_lock = threading.Lock()
_last_click_idx_time: dict[int, float] = {}
_last_click_idx_lock = threading.Lock()


running = True          # exchange 루프 제어 (cmd 1=시작, 2=중지)
_server_running = True  # accept 루프 제어 (q 입력 시에만 False)


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


def _next_request_id() -> int:
    global _request_id
    with _request_id_lock:
        _request_id += 1
        return _request_id


def _remember_pong(client: dict | None, resp: dict):
    if client is None or resp.get("status") != "pong":
        return
    client["mp"] = resp.get("mp", 0)
    client["hp"] = resp.get("hp", 0)
    client["max_hp"] = resp.get("max_hp", 0)
    client["available"] = int(client["mp"] // 20)


def _recv_expected_response(
    conn: socket.socket,
    *,
    req_id: int,
    expected_status: str,
    timeout: float,
    client: dict | None = None,
) -> dict | None:
    deadline = time.time() + timeout
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            conn.settimeout(remaining)
            resp = _recv_json(conn)
            if resp is None:
                return None

            status = resp.get("status")
            resp_req_id = resp.get("req_id")
            if status == expected_status and (resp_req_id == req_id or resp_req_id is None):
                return resp

            _remember_pong(client, resp)
            print(f"[server] 다른 요청 응답 무시: {resp}")
    finally:
        conn.settimeout(None)


def _try_use_potion(client: dict) -> bool:
    if client["available"] >= 2:
        return False
    now = time.time()
    if now - client["potion_last_used"] < POTION_COOLDOWN:
        return False

    if "conn" not in client:  # 서버 로컬
        macro.use_potion()
        client["potion_last_used"] = now
        return True

    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        req_id = _next_request_id()
        print(f"[server] 포션 전송 → {addr}")
        if _send_json(conn, {"cmd": "potion", "req_id": req_id}):
            ack = _recv_expected_response(
                conn,
                req_id=req_id,
                expected_status="ok",
                timeout=ACK_TIMEOUT,
                client=client,
            )
            if ack and ack.get("status") == "ok":
                client["potion_last_used"] = now
                print(f"[server] 포션 완료 ack 수신 from {addr}")
                return True
    return False


def _remove_client(client: dict):
    with _clients_lock:
        _clients[:] = [e for e in _clients if e is not client]
    try:
        client["conn"].close()
    except OSError:
        pass
    _recv_buffers.pop(client["conn"], None)
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

    client = {"conn": conn, "addr": addr, "lock": threading.Lock(), "mp": 0, "hp": 0, "max_hp": 0, "idx": idx, "available": 0, "potion_last_used": 0}
    with _clients_lock:
        _clients.append(client)

    _send_json(conn, {"cmd": "reset_coord"})
    print(f"[server] 좌표 초기화 전송 → {addr}")

    hp_zero_since: float | None = None

    try:
        while True:
            with client["lock"]:
                req_id = _next_request_id()
                if not _send_json(conn, {"cmd": "ping", "req_id": req_id}):
                    break
                resp = _recv_expected_response(
                    conn,
                    req_id=req_id,
                    expected_status="pong",
                    timeout=10,
                    client=client,
                )
                if resp is None:
                    break
                if resp.get("status") == "pong":
                    client["mp"] = resp.get("mp", 0)
                    client["hp"] = resp.get("hp", 0)
                    client["max_hp"] = resp.get("max_hp", 0)
                    client["available"] = int(client["mp"] // 20)
                    # print(f"[server] client {addr} HP: {client['hp']}  MP: {client['mp']}  available: {client['available']}")

                    if client["hp"] == 0:
                        if hp_zero_since is None:
                            hp_zero_since = time.time()
                        elif time.time() - hp_zero_since >= 60:
                            do_click = False
                            with _last_click_idx_lock:
                                if time.time() - _last_click_idx_time.get(idx, 0) >= 2:
                                    _last_click_idx_time[idx] = time.time()
                                    do_click = True
                            if do_click:
                                click_req_id = _next_request_id()
                                print(f"[server] HP 0 60초 지속 → 클릭 명령 전송: {addr}")
                                if _send_json(conn, {"cmd": "click", "x": 1080, "y": 174, "req_id": click_req_id}):
                                    ack = _recv_expected_response(
                                        conn,
                                        req_id=click_req_id,
                                        expected_status="ok",
                                        timeout=ACK_TIMEOUT,
                                        client=client,
                                    )
                                    if ack:
                                        print(f"[server] 클릭 ack 수신 from {addr}")
                                        hp_zero_since = time.time()
                    else:
                        hp_zero_since = None
            time.sleep(2)
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
def _send_pickup(client: dict, nickname: str | None = None) -> bool:
    """특정 클라이언트에게 pickup 명령을 보내고 ack를 기다린다."""
    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        req_id = _next_request_id()
        payload = {"cmd": "pickup", "target": "lineage1", "req_id": req_id}
        if nickname:
            payload["nickname"] = nickname
        if not _send_json(conn, payload):
            _remove_client(client)
            return False

        resp = _recv_expected_response(
            conn,
            req_id=req_id,
            expected_status="ok",
            timeout=ACK_TIMEOUT,
            client=client,
        )

        if resp is None:
            print(f"[server] ack 수신 실패 - 클라이언트 제거: {addr}")
            _remove_client(client)
            return False

        if resp.get("status") == "ok":
            print(f"[server] 픽업 완료 ack 수신 from {addr}")
            return True

        print(f"[server] 예상치 못한 응답: {resp}")
        return False


# ── 좌표 이동 브로드캐스트 ────────────────────────────────────────────────────
_client_coord_direction: str | None = None  # 마지막으로 클라이언트에 반영된 방향
_BLOCKED_AVOID_DIRS = ['southwest', 'west', 'northwest', 'north', 'northeast']


def _broadcast_move_coord(from_dir: str, to_dir: str):
    """방향 전환 시 모든 클라이언트에 좌표 이동 명령을 보내고 서버 자신도 적용."""
    global _client_coord_direction
    dx, dy = macro.DIRECTION_DELTAS[(from_dir, to_dir)]
    _client_coord_direction = to_dir
    if dx == 0 and dy == 0:
        return
    payload = {"cmd": "move_coord", "dx": dx, "dy": dy}
    with _clients_lock:
        clients = list(_clients)
    for c in clients:
        if "conn" not in c:
            continue
        with c["lock"]:
            _send_json(c["conn"], payload)
    macro.apply_coord_delta(dx, dy)
    print(f"[server] 좌표 브로드캐스트: {from_dir} → {to_dir}  (dx={dx:+}, dy={dy:+})")


# ── Exchange 루프 ──────────────────────────────────────────────────────────────
def exchange_loop():
    global running, _client_coord_direction

    # 첫 방향 맞춰줌
    macro._DIRECTION_FUNCS[macro.high_count_direction]()
    with _clients_lock:
        clients = list(_clients)
    for c in clients:
        if "conn" not in c:
            continue
        with c["lock"]:
            _send_json(c["conn"], {"cmd": "reset_coord"})
    

    WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)
    stage = WAIT_NICKNAME

    greeted_nickname = None
    adena_before = None
    prev_brightness = None
    brightness_changed = False
    _last_type_string_time = 0
    _last_status_print_time = 0
    _last_potion_idx_time: dict = {}
    clients_snapshot = []
    _last_target_text = ''
    _last_target_first_seen = 0.0
    _server_hp_zero_since: float | None = None  # 서버 자신 HP 0 지속 시작 시각
    _hp_full_since: float | None = None   # heal 모드에서 HP 100% 도달 시각
    mode = "service"
    while running:
        img = macro.screenshot(hwnd=macro.lineage1_hwnd)
        current_hp, max_hp = macro.read_hp(img)
        _mp1 = macro.read_mp(img)

        # ── HP가 0인 상태가 60초 지속되면 자체 재접속 클릭 ────────────────
        if current_hp == 0:
            if _server_hp_zero_since is None:
                _server_hp_zero_since = time.time()
            elif time.time() - _server_hp_zero_since >= 60:
                do_click = False
                with _last_click_idx_lock:
                    if time.time() - _last_click_idx_time.get(0, 0) >= 10:
                        _last_click_idx_time[0] = time.time()
                        do_click = True
                if do_click:
                    print("[server] 서버 HP 0 60초 지속 → 자체 클릭 실행")
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
                    time.sleep(2)
                    macro._DIRECTION_FUNCS[macro.high_count_direction]()
                    _server_hp_zero_since = time.time()
        else:
            _server_hp_zero_since = None

        # ── HP 100%가 아니면 heal 모드로 전환해 회복에 전념 ────────────────
        if current_hp != max_hp:
            print(f"[server] 현재 HP: {current_hp}/{max_hp}, MP: {_mp1}")
            _hp_full_since = None
            if mode == "service":
                macro.arduino_key_press(win32con.VK_F12)
                macro.arduino_key_press(win32con.VK_F2)
                mode = "heal"
            if _mp1 >= HEAL_MP_COST:
                macro.arduino_key_press(win32con.VK_F5)   # 힐 시전
                macro.arduino_key_press(win32con.VK_F5)   # 힐 시전
                time.sleep(0.1)
            else:
                macro.arduino_key_press(win32con.VK_F6)   # MP 부족 → 포션
                time.sleep(0.3)
            continue
        else:
            if mode == "heal":
                # HP 100%가 30초 이상 지속되어야 service 모드로 복귀
                if _hp_full_since is None:
                    _hp_full_since = time.time()
                if time.time() - _hp_full_since >= 30:
                    macro.arduino_key_press(win32con.VK_F12)
                    macro.arduino_key_press(win32con.VK_F1)
                    mode = "service"
                    _hp_full_since = None
                else:
                    time.sleep(0.5)
                    continue

        # ── Stage 1: MP 읽기 / 방향 조정 / 광고 / 닉네임 대기 ──────────────
        if stage == WAIT_NICKNAME:
            if _mp1 != 0:
                macro.mp_1 = _mp1

            with _clients_lock:
                for e in _clients:
                    if "conn" not in e:
                        e["mp"] = macro.mp_1
                        e["available"] = int(macro.mp_1 // 20)
                        break
                clients_snapshot = list(_clients)

            for e in clients_snapshot:
                elapsed = time.time() - _last_potion_idx_time.get(e["idx"], 0)
                if elapsed < SAME_UNIT_DELAY:
                    time.sleep(SAME_UNIT_DELAY - elapsed)
                if _try_use_potion(e):
                    _last_potion_idx_time[e["idx"]] = time.time()
                    if e["idx"] == 0 and "conn" in e:
                        time.sleep(0.5)
                        macro.force_set_foreground_window(macro.lineage1_hwnd)

            total_count = sum(e["available"] for e in clients_snapshot)
            if time.time() - _last_status_print_time >= 3:
                for e in clients_snapshot:
                    print(f"idx({e['idx']}): MP: {e['mp']}, 잔여: {e['available']}")
                print(f"총 {total_count}")
                _last_status_print_time = time.time()

            if total_count < macro.direction_threshold:
                if macro.current_direction != macro.low_count_direction:
                    macro.force_set_foreground_window(macro.lineage1_hwnd)
                    from_dir = macro.current_direction
                    macro._DIRECTION_FUNCS[macro.low_count_direction]()
                    _broadcast_move_coord(from_dir, macro.low_count_direction)
                    time.sleep(1)
                time.sleep(0.5)
                continue
            else:
                if macro.current_direction == macro.low_count_direction:
                    from_dir = macro.current_direction
                    macro.force_set_foreground_window(macro.lineage1_hwnd)
                    time.sleep(1)
                    macro._DIRECTION_FUNCS[macro.high_count_direction]()
                    _broadcast_move_coord(from_dir, macro.high_count_direction)
                    time.sleep(1)

            if time.time() - _last_type_string_time >= 14:
                _ad_formats = [
                    f"헤이 {macro.adena_per_pickup} {total_count}방 !",
                    f"{total_count}방 가능 한방에 {macro.adena_per_pickup}아데나!",
                    f"헤이 {macro.adena_per_pickup}  6방 {macro.adena_per_pickup * 6}",
                ]
                macro.arduino_type_string(random.choice(_ad_formats))
                _last_type_string_time = time.time()

            nickname = macro.readExchangeNickname(img=img)
            if nickname:
                greeted_nickname = nickname
                # macro.arduino_type_string(f"최대 {total_count}방 입니다! 확인!")
                stage = READ_ADENA
                continue

            input_text = macro.has_target_in_input()
            if input_text:
                if input_text in macro.blocked_list:
                    candidates = [d for d in _BLOCKED_AVOID_DIRS if d != macro.current_direction]
                    new_dir = random.choice(candidates)
                    from_dir = macro.current_direction
                    macro.force_set_foreground_window(macro.lineage1_hwnd)
                    macro._DIRECTION_FUNCS[new_dir]()
                    _broadcast_move_coord(from_dir, new_dir)
                    print(f"[server] blocked 감지 ({input_text}) → 방향 전환: {new_dir}")
                    _last_target_text = ''
                    _last_target_first_seen = 0.0
                else:
                    if input_text == _last_target_text:
                        if time.time() - _last_target_first_seen >= 60:
                            macro.add_to_blocked_list(input_text)
                            print(f"[server] 60초 지속 타겟 → blocked_list 자동 추가: {input_text}")
                            _last_target_text = ''
                            _last_target_first_seen = 0.0
                    else:
                        _last_target_text = input_text
                        _last_target_first_seen = time.time()
                    macro._arduino_send(f'KP,{win32con.VK_F7}')
            else:
                _last_target_text = ''
                _last_target_first_seen = 0.0
            time.sleep(0.5)

        # ── Stage 2: 교환 전 아데나 1회 측정 ────────────────────────────────
        elif stage == READ_ADENA:
            if not macro.readExchangeNickname(img):
                stage = WAIT_NICKNAME
                continue
            adena_before = macro.readAdena()
            macro._arduino_send(f'KP,{win32con.VK_F7}')
            stage = MONITOR_BRIGHTNESS

        # ── Stage 3: 슬롯 밝기 감시 → 변화 시 교환 수락 ────────────────────
        elif stage == MONITOR_BRIGHTNESS:
            img = macro.screenshot()
            nickname = macro.readExchangeNickname(img)
            if not nickname:
                stage = PICKUP
                continue

            if nickname in macro.blocked_list:
                print(f"[server] blocked 닉네임 감지 ({nickname}) → 교환 거절")
                macro.rejectExchange()
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

            slot = macro.crop(img, 258, 677, 30, 30)
            brightness = macro.get_brightness(slot)
            print(f"[server] 슬롯 밝기: {brightness:.2f}")

            if (prev_brightness is not None) and (brightness != prev_brightness or brightness > 110):
                brightness_changed = True
                macro.acceptExchange()
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
            print(f"[server] 아데나 변화 감지: {adena_before} → {adena_after}")
            received = adena_after - adena_before
            pickup_count = int(received // macro.adena_per_pickup)

            # 핑 스레드의 concurrent 업데이트와 격리하기 위해 available을 별도 dict로 복사
            # clients_snapshot은 shallow copy라 핑 스레드가 동일 dict를 수정하므로,
            # pickup loop 전용 카운터를 따로 유지한다.
            pickup_avail: dict[int, int] = {id(c): c["available"] for c in clients_snapshot}
            total_available = sum(pickup_avail.values())
            remaining = min(macro.direction_threshold, pickup_count)
            print(f"remaining pickup count: {remaining} (received: {received}, available: {total_available})")

            # ── 픽업 분배 ───────────────────────────────────────────────────
            # 매 라운드: 전체 중 available 최댓값 탐색
            #   → 공유자 여럿이면 idx 내림차순 모두 실행
            #   → 혼자면 해당 client만 실행
            # 같은 idx는 SAME_UNIT_DELAY 이내 재전송 금지
            last_idx_time: dict = {}

            while remaining > 0:
                with_avail = [c for c in clients_snapshot if pickup_avail[id(c)] > 0]
                if not with_avail:
                    break

                max_avail = max(pickup_avail[id(c)] for c in with_avail)
                candidates = sorted(
                    [c for c in with_avail if pickup_avail[id(c)] == max_avail],
                    key=lambda c: c["idx"], reverse=True
                )

                sent_any = False
                for c in candidates:
                    if remaining <= 0:
                        break
                    elapsed = time.time() - last_idx_time.get(c["idx"], 0)
                    if elapsed < SAME_UNIT_DELAY:
                        time.sleep(SAME_UNIT_DELAY - elapsed)

                    if "conn" not in c:
                        print(f"[서버 픽업 실행] - (남은 픽업: {remaining})")
                        macro.pickup_lineage1(target_nickname=greeted_nickname)
                        ok = True
                    else:
                        print(f"[서버 → 클라이언트 픽업] idx: {c['idx']} - (남은 픽업: {remaining})")
                        ok = _send_pickup(c, nickname=greeted_nickname)

                    last_idx_time[c["idx"]] = time.time()
                    if ok:
                        remaining -= 1
                        pickup_avail[id(c)] -= 1
                        sent_any = True

                if not sent_any:
                    if remaining > 0:
                        print(f"[server] 픽업 명령 전송 실패 - 남은 픽업: {remaining}")
                    break

            if win32gui.GetForegroundWindow() != macro.lineage1_hwnd:
                macro.force_set_foreground_window(macro.lineage1_hwnd)
            time.sleep(0.1)
            if received > 0:
                # display_name = greeted_nickname[:2] if len(greeted_nickname) > 2 else greeted_nickname
                macro.arduino_type_string(f"감삼당~!")

            if macro.current_direction != macro.high_count_direction:
                from_dir = macro.current_direction
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                macro._DIRECTION_FUNCS[macro.high_count_direction]()
                _broadcast_move_coord(from_dir, macro.high_count_direction)

            stage = WAIT_NICKNAME
            greeted_nickname = None
            adena_before = None
            prev_brightness = None
            brightness_changed = False


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    macro.init_setting("server")
    _client_coord_direction = macro.current_direction

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[server] 대기 중: {HOST}:{PORT}")

    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()

    # 서버 자신을 idx=0 으로 _clients에 등록 (conn/addr/lock 없음)
    with _clients_lock:
        _clients.append({"idx": 0, "mp": 0, "hp": 0, "max_hp": 0, "available": 0, "potion_last_used": 0})

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
            if exchange_thread and exchange_thread.is_alive():
                print("[server] exchange 이미 실행 중")
            else:
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                running = True
                exchange_thread = threading.Thread(target=exchange_loop, daemon=True)
                exchange_thread.start()
        if cmd == "2":
            running = False
        if cmd == "3":
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro.arduino_key_press(win32con.VK_F10)
            win32api.SetCursorPos((105, 85))
            time.sleep(1)
            macro.arduino_mouse_click_left()
            macro.arduino_mouse_click_left()
