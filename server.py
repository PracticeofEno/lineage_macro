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
LOW_MP_AVAILABLE_THRESHOLD = 2
HASTE_CHECK_DEFAULT_INTERVAL = 3.0
DIRECTION_RETURN_CHECK_INTERVAL = 30.0
TURN_DIRECTIONS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)

# ── 클라이언트 관리 ───────────────────────────────────────────────────────────
# client: {"conn": socket, "addr": tuple, "lock": Lock, "mp": int, "idx": int}
# idx  : 클라이언트 실행 시 인자로 지정 (0=서버와 같은 PC, 같은 PC끼리 동일 idx 사용)
# lock : ping-pong과 pickup 명령이 같은 소켓을 동시에 사용하지 않도록 보호
_clients: list[dict] = []
_clients_lock = threading.Lock()


running = True          # exchange 루프 제어 (cmd 1=시작, 2=중지)
_server_running = True  # accept 루프 제어 (q 입력 시에만 False)


def _read_direction_change_nicknames(data: dict) -> set[str]:
    raw_nicknames = data.get("direction_change_nicknames", data.get("direction_change_nickname", []))
    if isinstance(raw_nicknames, str):
        nicknames = [raw_nicknames]
    elif isinstance(raw_nicknames, (list, tuple, set)):
        nicknames = raw_nicknames
    else:
        nicknames = []
    return {str(n).strip() for n in nicknames if str(n).strip()}


def _read_blocked_turn_directions(data: dict) -> set[str]:
    raw_directions = data.get("blocked_turn_directions", [])
    if isinstance(raw_directions, str):
        directions = [raw_directions]
    elif isinstance(raw_directions, (list, tuple, set)):
        directions = raw_directions
    else:
        directions = []
    return {str(direction).strip() for direction in directions if str(direction).strip()}


def _load_macro_data() -> dict:
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)


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


def _try_use_potion(client: dict) -> bool:
    if client["available"] > LOW_MP_AVAILABLE_THRESHOLD:
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
        print(f"[server] 포션 전송 → {addr}")
        if _send_json(conn, {"cmd": "potion"}):
            conn.settimeout(ACK_TIMEOUT)
            ack = _recv_json(conn)
            conn.settimeout(None)
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

    client = {"conn": conn, "addr": addr, "lock": threading.Lock(), "mp": 0, "idx": idx, "available": 0, "potion_last_used": 0}
    with _clients_lock:
        _clients.append(client)
    try:
        while True:
            with client["lock"]:
                if not _send_json(conn, {"cmd": "ping"}):
                    break
                conn.settimeout(10)
                resp = _recv_json(conn)
                conn.settimeout(None)
                if resp is None:
                    break
                if resp.get("status") == "pong":
                    mp = resp.get("mp")
                    if mp is not None:
                        client["mp"] = int(mp)
                        client["available"] = int(client["mp"] // 20)
                    # print(f"[server] client {addr} MP: {client['mp']}  available: {client['available']}")
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
def _send_pickup(client: dict, nickname: str | None = None, direction: str | None = None) -> bool:
    """특정 클라이언트에게 pickup 명령을 보내고 ack를 기다린다."""
    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        payload = {"cmd": "pickup", "target": "lineage1"}
        if nickname:
            payload["nickname"] = nickname
        if direction:
            payload["direction"] = direction
        if not _send_json(conn, payload):
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


def _load_haste_check_config(direction: str) -> tuple[tuple[int, int], float, set[str]]:
    data = _load_macro_data()

    xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=direction)

    interval = float(data.get("haste_check_interval_seconds", HASTE_CHECK_DEFAULT_INTERVAL))
    if interval <= 0:
        interval = HASTE_CHECK_DEFAULT_INTERVAL

    direction_change_nicknames = _read_direction_change_nicknames(data)

    return xy, interval, direction_change_nicknames


def _read_adena_after_exchange(adena_before: int | None, timeout: float = 6.0) -> int | None:
    deadline = time.time() + timeout
    last_value = None

    while True:
        value = macro.readAdena(max_attempts=3)
        if value is not None:
            last_value = value
            if adena_before is None or value > adena_before:
                return value

        if time.time() >= deadline:
            return last_value

        print(f"[server] 아데나 재확인 중: before={adena_before}, current={last_value}")
        time.sleep(0.5)


def _clear_chat_input() -> None:
    macro.arduino_key_down(win32con.VK_CONTROL)
    macro.arduino_key_press(win32con.VK_BACK)
    macro.arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)


def _read_nickname_at_xy(check_xy: tuple[int, int]) -> str:
    x, y = check_xy
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_mouse_shift_click_right(x, y)
    time.sleep(0.15)

    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
    nickname = macro.readInputText(img).strip()
    _clear_chat_input()
    return nickname


def _try_haste_front_person(check_xy: tuple[int, int], direction_change_nicknames: set[str]) -> str | None:
    nickname = _read_nickname_at_xy(check_xy)
    if not nickname:
        print(f"[haste] 앞사람 감지 없음 at {check_xy}")
        return None

    if nickname in direction_change_nicknames:
        direction = macro.turn_random_excluding(macro.low_count_direction)
        if direction is None:
            print(f"[haste] 지정 닉네임 감지: '{nickname}' at {check_xy} -> 거래신청 안 함, 랜덤 방향 전환 실패")
            return None
        print(f"[haste] 지정 닉네임 감지: '{nickname}' at {check_xy} -> 거래신청 안 함, {direction} 전환")
        return direction

    print(f"[haste] 앞사람 감지: '{nickname}' at {check_xy} -> F7")
    macro._arduino_send(f'KP,{win32con.VK_F7}')
    return "haste"


def _choose_recovered_shop_direction(preferred_direction: str) -> str | None:
    data = _load_macro_data()
    direction_change_nicknames = _read_direction_change_nicknames(data)
    blocked_directions = _read_blocked_turn_directions(data)
    excluded_directions = {macro.low_count_direction, *blocked_directions}

    candidates: list[str] = []
    if preferred_direction in TURN_DIRECTIONS and preferred_direction not in excluded_directions:
        candidates.append(preferred_direction)

    fallback_candidates = [
        direction
        for direction in TURN_DIRECTIONS
        if direction not in excluded_directions and direction != preferred_direction
    ]
    random.shuffle(fallback_candidates)
    candidates.extend(fallback_candidates)

    if not candidates:
        print(
            "[server] MP 회복 후 방향 후보 없음: "
            f"preferred={preferred_direction}, excluded={sorted(excluded_directions)}"
        )
        return None

    for direction in candidates:
        check_xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=direction)
        nickname = _read_nickname_at_xy(check_xy)
        if nickname in direction_change_nicknames:
            reason = "기본 방향" if direction == preferred_direction else "대체 방향"
            print(f"[server] MP 회복 후 {reason} 차단: '{nickname}' at {check_xy} -> {direction}")
            continue

        if direction == preferred_direction:
            print(
                "[server] MP 회복 후 방향 전환 허용: "
                f"지정 닉네임 없음('{nickname}') at {check_xy} -> {direction}"
            )
        else:
            print(
                "[server] MP 회복 후 대체 방향 선택: "
                f"지정 닉네임 없음('{nickname}') at {check_xy} -> {direction}"
            )
        return direction

    print("[server] MP 회복 후 방향 전환 실패: 모든 후보 방향에 지정 닉네임 감지")
    return None


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
    _last_status_print_time = 0
    _last_potion_idx_time: dict = {}
    _last_haste_check_time = 0
    _last_return_check_time = 0.0
    base_shop_direction = macro.high_count_direction
    shop_direction = base_shop_direction
    was_low_mp = False
    clients_snapshot = []
    prev_stage = None
    direction_synced = False

    while running:
        # 이전 stage가 READ_ADENA 이상이었을 경우 WAIT_NICKNAME 복귀 시 TAB + 타겟 리셋
        if stage != prev_stage:
            if stage == WAIT_NICKNAME and prev_stage is not None and prev_stage >= READ_ADENA:
                macro.key_press(win32con.VK_TAB)
                time.sleep(0.3)
            prev_stage = stage

        # ── Stage 1: MP 읽기 / 방향 조정 / 광고 / 닉네임 대기 ──────────────
        if stage == WAIT_NICKNAME:
            if not direction_synced:
                if macro.sync_direction(force=True):
                    print(f"[server] 시작 방향 동기화: {macro.current_direction}")
                if macro.turn_to(shop_direction):
                    print(f"[server] 장사 방향 설정: {shop_direction}")
                direction_synced = True

            img = macro.screenshot(hwnd=macro.lineage1_hwnd)
            _mp1 = macro.readMp(img)
            if _mp1 is not None:
                macro.mp_1 = _mp1
            else:
                macro.press_ctrl_a_for_mp_retry()

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
            should_face_low = total_count < macro.direction_threshold
            if time.time() - _last_status_print_time >= 3:
                for e in clients_snapshot:
                    print(f"idx({e['idx']}): MP: {e['mp']}, 잔여: {e['available']}")
                status_xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=shop_direction)
                print(f"[server] 현재 방향: {shop_direction}, 좌표: {status_xy}")
                _last_status_print_time = time.time()

            if should_face_low:
                was_low_mp = True
                if macro.turn_to(macro.low_count_direction):
                    print(f"[server] 저MP 감지 -> {macro.low_count_direction}")
                if time.time() - _last_type_string_time >= 16:
                    macro.arduino_type_string("죄송합니다. 마나회복중입니다.")
                    _last_type_string_time = time.time()
                time.sleep(0.5)
                continue
            else:
                if was_low_mp:
                    recover_direction = _choose_recovered_shop_direction(macro.high_count_direction)
                    if recover_direction is None:
                        time.sleep(0.5)
                        continue

                    shop_direction = recover_direction
                    was_low_mp = False

                if macro.turn_to(shop_direction):
                    print(f"[server] 장사 방향 유지 -> {shop_direction}")
                    img = macro.screenshot(hwnd=macro.lineage1_hwnd)

            if time.time() - _last_type_string_time >= 10:
                _ad_formats = [
                    f"1방 {macro.adena_per_pickup}원 6방 {macro.adena_per_pickup * 6}원",
                ]
                macro.arduino_type_string(random.choice(_ad_formats))
                _last_type_string_time = time.time()

            haste_check_xy, haste_check_interval, direction_change_nicknames = _load_haste_check_config(shop_direction)

            nickname = macro.readExchangeNickname(img=img)
            if nickname:
                if nickname in direction_change_nicknames:
                    print(f"[server] 지정 닉네임 거래창 감지: '{nickname}' -> ESC")
                    macro.key_press(win32con.VK_ESCAPE)
                    time.sleep(0.5)
                    continue

                greeted_nickname = nickname
                # macro.arduino_type_string(f"\\f2{greeted_nickname}\\f7님 어서오세요!")
                stage = READ_ADENA
                continue

            if (
                shop_direction != base_shop_direction
                and time.time() - _last_return_check_time >= DIRECTION_RETURN_CHECK_INTERVAL
            ):
                _last_return_check_time = time.time()
                base_check_xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=shop_direction)
                base_nickname = _read_nickname_at_xy(base_check_xy)

                if base_nickname in direction_change_nicknames:
                    print(f"[server] 원래 자리 확인: '{base_nickname}' 감지 at {base_check_xy} -> {shop_direction} 유지")
                    time.sleep(0.5)
                    continue

                print(f"[server] 원래 자리 확인: 지정 닉네임 없음('{base_nickname}') at {base_check_xy} -> {shop_direction} 유지")
                time.sleep(0.5)
                continue

            if time.time() - _last_haste_check_time >= haste_check_interval:
                _last_haste_check_time = time.time()
                haste_result = _try_haste_front_person(haste_check_xy, direction_change_nicknames)
                if haste_result and haste_result != "haste":
                    shop_direction = haste_result
                    _last_return_check_time = time.time()
                    print(f"[server] 장사 방향 변경: {shop_direction}")
                if haste_result:
                    time.sleep(0.5)
                    continue

            time.sleep(0.5)

        # ── Stage 2: 교환 전 아데나 1회 측정 ────────────────────────────────
        elif stage == READ_ADENA:
            exchange_nickname = macro.readExchangeNickname(img)
            if not exchange_nickname:
                stage = WAIT_NICKNAME
                continue
            _, _, direction_change_nicknames = _load_haste_check_config(shop_direction)
            if exchange_nickname in direction_change_nicknames:
                print(f"[server] 지정 닉네임 거래신청 차단: '{exchange_nickname}' -> ESC")
                macro.key_press(win32con.VK_ESCAPE)
                stage = WAIT_NICKNAME
                time.sleep(0.5)
                continue
            adena_before = macro.readAdena()
            macro._arduino_send(f'KP,{win32con.VK_F7}')
            stage = MONITOR_BRIGHTNESS

        # ── Stage 3: 슬롯 밝기 감시 → 변화 시 교환 수락 ────────────────────
        elif stage == MONITOR_BRIGHTNESS:
            img = macro.screenshot()
            if not macro.readExchangeNickname(img):
                stage = PICKUP
                continue

            slot = macro.crop(img, 258, 677, 30, 30)
            brightness = macro.get_brightness(slot)
            print(f"[server] 슬롯 밝기: {brightness:.2f}")

            if prev_brightness is not None and brightness != prev_brightness:
                brightness_changed = True
                macro.acceptExchange()
            prev_brightness = brightness
            time.sleep(0.5)

        # ── Stage 4: 받은 아데나 계산 → 서버/클라이언트 픽업 분배 ──────────
        elif stage == PICKUP:
            adena_after = _read_adena_after_exchange(adena_before)
            if adena_before is None or adena_after is None:
                print(f"[server] 아데나 읽기 실패: before={adena_before}, after={adena_after}")
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

            print(f"[server] 아데나 변화 감지: {adena_before} → {adena_after} (slot_changed={brightness_changed})")
            received = adena_after - adena_before
            if received <= 0:
                print(f"[server] 아데나 증가 없음: received={received}")
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

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
                        macro.pickup_lineage1(target_nickname=greeted_nickname, direction=shop_direction)
                        ok = True
                    else:
                        print(f"[서버 → 클라이언트 픽업] idx: {c['idx']} - (남은 픽업: {remaining})")
                        ok = _send_pickup(c, nickname=greeted_nickname, direction=shop_direction)

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
                macro.arduino_type_string(f"감사합니다!")
                _last_type_string_time = time.time()
                time.sleep(2.5)

            stage = WAIT_NICKNAME
            greeted_nickname = None
            adena_before = None
            prev_brightness = None
            brightness_changed = False


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    macro.init_setting("server")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[server] 대기 중: {HOST}:{PORT}")

    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()

    # 서버 자신을 idx=0 으로 _clients에 등록 (conn/addr/lock 없음)
    with _clients_lock:
        _clients.append({"idx": 0, "mp": 0, "available": 0, "potion_last_used": 0})

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
            with _clients_lock:
                target = next((c for c in _clients if c.get("idx") == 1 and "conn" in c), None)
            if target:
                _send_pickup(target, direction=macro.current_direction)
            else:
                print("[server] idx=1 클라이언트 없음")
