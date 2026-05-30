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

# 서버가 client.py 연결을 받을 IP입니다. 0.0.0.0은 현재 PC의 모든 네트워크에서 받겠다는 뜻입니다.
HOST = '0.0.0.0'

# client.py가 접속할 TCP 포트입니다. client 쪽 포트와 반드시 같아야 합니다.
PORT = 9999

# server가 client에게 pickup 명령을 보낸 뒤 응답을 기다리는 최대 시간(초)입니다.
ACK_TIMEOUT = 10

# F10 슬롯 비우기는 키를 최대 30초 유지할 수 있어서 일반 ACK보다 길게 기다립니다.
F10_CLEAR_ACK_TIMEOUT = 35

# 같은 idx, 즉 같은 PC 안에서 서버/클라이언트 픽업 명령이 너무 붙지 않게 벌리는 시간(초)입니다.
SAME_UNIT_DELAY = 0.5

# MP 포션 사용 후 다시 사용할 수 있을 때까지 기다리는 시간(초)입니다.
POTION_COOLDOWN = 600

# 개별 창의 헤이스트 가능 횟수가 이 값 이하이면 MP 포션 사용 후보로 봅니다.
# macro_data.json의 direction_threshold와 다릅니다. 이 값은 "포션 사용 기준"입니다.
LOW_MP_AVAILABLE_THRESHOLD = 1

# macro_data.json의 haste_check_interval_seconds가 없거나 잘못됐을 때만 쓰는 기본값입니다.
HASTE_CHECK_DEFAULT_INTERVAL = 3.0

# 앞사람에게 F7을 누른 뒤 거래창/상태가 뜰 시간을 주기 위해 기다리는 시간(초)입니다.
HASTE_AFTER_F7_WAIT_SECONDS = 0.2

# 거래창이 열린 뒤 밝기 변화를 확인하는 반복 주기(초)입니다.
EXCHANGE_MONITOR_INTERVAL_SECONDS = 0.25

# macro_data.json의 same_front_nickname_chat_seconds가 없거나 잘못됐을 때만 쓰는 기본값입니다.
SAME_FRONT_NICKNAME_CHAT_DEFAULT_SECONDS = 60.0

# macro_data.json의 same_front_nickname_chat_cooldown_seconds가 없거나 잘못됐을 때만 쓰는 기본값입니다.
SAME_FRONT_NICKNAME_CHAT_DEFAULT_COOLDOWN_SECONDS = 60.0

# macro_data.json의 low_mp_message_interval_seconds가 없거나 잘못됐을 때만 쓰는 기본값입니다.
LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS = 20.0

# macro_data.json의 low_mp_messages가 없거나 비어 있을 때만 쓰는 기본 안내 문구 목록입니다.
LOW_MP_MESSAGES_DEFAULT = ('\'')

# 장사 가능 상태에서 방향 클릭이 씹힌 경우를 보정하려고 같은 장사 방향을 다시 누르는 간격(초)입니다.
SHOP_DIRECTION_FORCE_INTERVAL_SECONDS = 15.0

# 기본 장사 방향이 아닌 방향에 있을 때 원래 자리 확인을 다시 시도하는 간격(초)입니다.
DIRECTION_RETURN_CHECK_INTERVAL = 30.0

# 거래창 슬롯 영역 평균 밝기가 이 값보다 크면 교환 OK 후보로 판단합니다.
EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD = 120.0

# 상대방 거래창에 아데나 외 이미지가 올라왔을 때 취소 후 입력할 안내 문구입니다.
INVALID_TRADE_ITEM_MESSAGE = "아데나만 올려주세요."

# 자동 방향전환 후보로 쓰는 8방향 이름입니다. macro_data.json의 방향별 좌표 키와 맞아야 합니다.
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
_macro_data_write_lock = threading.Lock()
_restart_shutdown_lock = threading.Lock()
_restart_shutdown_started = False


running = True          # exchange 루프 제어 (cmd 1=시작, 2=중지)
_server_running = True  # accept 루프 제어 (q 입력 시에만 False)
_f12_stop_reported = False


def _is_f12_pressed() -> bool:
    state = win32api.GetAsyncKeyState(win32con.VK_F12)
    return bool(state & 0x8000 or state & 0x0001)


def _request_f12_stop() -> bool:
    global running, _f12_stop_reported
    if not _is_f12_pressed():
        return False
    running = False
    if not _f12_stop_reported:
        print("[server] F12 emergency stop")
        _f12_stop_reported = True
    return True


def _sleep_interruptible(seconds: float) -> bool:
    deadline = time.time() + max(0.0, seconds)
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if _request_f12_stop():
            return True
        time.sleep(min(0.05, remaining))
    return False


def _normalize_nickname_list(raw_nicknames) -> list[str]:
    if isinstance(raw_nicknames, str):
        nicknames = [raw_nicknames]
    elif isinstance(raw_nicknames, (list, tuple, set)):
        nicknames = raw_nicknames
    else:
        nicknames = []

    normalized: list[str] = []
    seen: set[str] = set()
    for nickname in nicknames:
        value = str(nickname).strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _read_direction_change_nicknames(data: dict) -> set[str]:
    raw_nicknames = data.get("direction_change_nicknames", data.get("direction_change_nickname", []))
    return set(_normalize_nickname_list(raw_nicknames))


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


def _write_macro_data(data: dict) -> None:
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    tmp_path = f"{data_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, data_path)


def _add_direction_change_nickname(nickname: str) -> bool:
    nickname = str(nickname).strip()
    if not nickname:
        return False

    with _macro_data_write_lock:
        data = _load_macro_data()
        nicknames = _normalize_nickname_list(
            data.get("direction_change_nicknames", data.get("direction_change_nickname", []))
        )
        if nickname in nicknames:
            return False

        nicknames.append(nickname)
        data["direction_change_nicknames"] = nicknames
        _write_macro_data(data)

    print(f"[server] 지정 닉네임 자동 추가 - nickname='{nickname}'")
    return True


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
        print(f"[server] 포션 명령 전송 - client_idx={client.get('idx')}, addr={addr}")
        if _send_json(conn, {"cmd": "potion"}):
            conn.settimeout(ACK_TIMEOUT)
            ack = _recv_json(conn)
            conn.settimeout(None)
            if ack:
                for line in ack.get("logs", []):
                    print(f"[client idx({client.get('idx')})] {line}")
            if ack and ack.get("status") == "ok":
                client["potion_last_used"] = now
                print(f"[server] 포션 응답 수신 - client_idx={client.get('idx')}, status=ok, addr={addr}")
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


def _send_restart(client: dict) -> bool:
    """특정 클라이언트에게 Restart 클릭을 시도하게 하고 ack를 기다린다."""
    if "conn" not in client:
        return False

    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        print(f"[server] Restart 명령 전송 - client_idx={client.get('idx')}, addr={addr}")
        if not _send_json(conn, {"cmd": "restart"}):
            _remove_client(client)
            return False

        conn.settimeout(ACK_TIMEOUT)
        resp = _recv_json(conn)
        conn.settimeout(None)

        if resp is None:
            print(f"[server] Restart 응답 실패 - client_idx={client.get('idx')}, addr={addr}")
            _remove_client(client)
            return False

        for line in resp.get("logs", []):
            print(f"[client idx({client.get('idx')})] {line}")

        if resp.get("status") == "stopped":
            print(
                f"[server] Restart 응답 수신 - client_idx={client.get('idx')}, "
                f"clicked={resp.get('clicked')}, addr={addr}"
            )
            return True

        print(f"[server] Restart 응답 오류 - client_idx={client.get('idx')}, resp={resp}")
        return False


def _send_clear_f10(client: dict) -> bool:
    """특정 클라이언트에게 F10 슬롯을 비우도록 명령하고 ack를 기다린다."""
    if "conn" not in client:
        return False

    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        print(f"[server] F10 슬롯 비우기 명령 전송 - client_idx={client.get('idx')}, addr={addr}")
        if not _send_json(conn, {"cmd": "clear_f10"}):
            _remove_client(client)
            return False

        conn.settimeout(F10_CLEAR_ACK_TIMEOUT)
        resp = _recv_json(conn)
        conn.settimeout(None)

        if resp is None:
            print(f"[server] F10 슬롯 비우기 응답 실패 - client_idx={client.get('idx')}, addr={addr}")
            _remove_client(client)
            return False

        for line in resp.get("logs", []):
            print(f"[client idx({client.get('idx')})] {line}")

        if resp.get("status") == "ok":
            print(
                f"[server] F10 슬롯 비우기 응답 수신 - client_idx={client.get('idx')}, "
                f"held_seconds={resp.get('held_seconds')}, addr={addr}"
            )
            return True

        print(f"[server] F10 슬롯 비우기 응답 오류 - client_idx={client.get('idx')}, resp={resp}")
        return False


def _request_clients_clear_f10() -> None:
    with _clients_lock:
        clients_snapshot = [e for e in _clients if "conn" in e]

    for client in clients_snapshot:
        _send_clear_f10(client)


def _request_restart_shutdown(
    source: str,
    *,
    skip_client: dict | None = None,
    click_server: bool = False,
) -> None:
    """Restart 감지 후 서버/클라이언트 클릭을 한 번만 조율하고 exchange를 멈춘다."""
    global running, _restart_shutdown_started

    with _restart_shutdown_lock:
        if _restart_shutdown_started:
            return
        _restart_shutdown_started = True

    print(f"[server] Restart 감지 - source={source}")

    if click_server:
        try:
            clicked = macro.click_restart_if_visible()
            print(f"[server] Restart 서버 클릭 시도 - clicked={clicked}")
        except Exception as exc:
            print(f"[server] Restart 서버 클릭 실패 - error={exc}")

    with _clients_lock:
        clients_snapshot = [
            e for e in _clients
            if "conn" in e and e is not skip_client
        ]

    if clients_snapshot:
        print(f"[server] Restart 클라이언트 클릭 요청 - count={len(clients_snapshot)}")
    else:
        print("[server] Restart 클라이언트 클릭 요청 스킵 - reason=no_connected_client")

    for client in clients_snapshot:
        _send_restart(client)

    running = False
    print("[server] Restart 처리 완료 - exchange macro stopped")


def _handle_restart_watcher_click() -> None:
    _request_restart_shutdown("watcher", click_server=False)


def _clear_server_f10_slot_if_needed(img=None) -> bool:
    held_seconds = macro.clear_f10_slot_if_occupied(img=img)
    if held_seconds <= 0:
        return False
    print(f"[server] F10 슬롯 비우기 완료 - held_seconds={held_seconds:.1f}")
    _request_clients_clear_f10()
    return True


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
            restart_detected = False
            with client["lock"]:
                if not _send_json(conn, {"cmd": "ping"}):
                    break
                conn.settimeout(F10_CLEAR_ACK_TIMEOUT)
                resp = _recv_json(conn)
                conn.settimeout(None)
                if resp is None:
                    break
                for line in resp.get("logs", []):
                    print(f"[client idx({client.get('idx')})] {line}")
                if resp.get("status") == "stopped":
                    restart_detected = True
                elif resp.get("status") == "pong":
                    mp = resp.get("mp")
                    if mp is not None:
                        client["mp"] = int(mp)
                        client["available"] = int(client["mp"] // 20)
                    # print(f"[server] client {addr} MP: {client['mp']}  available: {client['available']}")
            if restart_detected:
                _request_restart_shutdown(
                    f"client_idx={client.get('idx')}",
                    skip_client=client,
                    click_server=True,
                )
                break
            if _sleep_interruptible(2):
                break
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
def _send_pickup(client: dict, nickname: str | None = None, direction: str | None = None) -> str:
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
            return "failed"

        conn.settimeout(ACK_TIMEOUT)
        resp = _recv_json(conn)
        conn.settimeout(None)

        if resp is None:
            print(f"[server] 픽업 응답 실패 - client_idx={client.get('idx')}, addr={addr}")
            _remove_client(client)
            return "failed"

        for line in resp.get("logs", []):
            print(f"[client idx({client.get('idx')})] {line}")

        if resp.get("status") == "ok":
            print(f"[server] 픽업 응답 수신 - client_idx={client.get('idx')}, status=ok, addr={addr}")
            return "ok"

        if resp.get("status") == "target_failed":
            print(f"[server] 픽업 응답 수신 - client_idx={client.get('idx')}, status=target_failed, addr={addr}")
            return "target_failed"

        print(f"[server] 픽업 응답 오류 - client_idx={client.get('idx')}, resp={resp}")
        return "failed"


def _select_chat_client(clients_snapshot: list[dict], preferred_idx: int | None) -> dict | None:
    connected_clients = [c for c in clients_snapshot if "conn" in c]
    if not connected_clients:
        return None

    if preferred_idx is not None:
        preferred = next((c for c in connected_clients if c.get("idx") == preferred_idx), None)
        if preferred is not None:
            return preferred

    return connected_clients[0]


def _send_client_chat(client: dict, message: str) -> bool:
    """특정 클라이언트 창에서 채팅을 입력하도록 명령하고 ack를 기다린다."""
    if not message or "conn" not in client:
        return False

    conn = client["conn"]
    addr = client["addr"]
    with client["lock"]:
        if not _send_json(conn, {"cmd": "chat", "message": message}):
            _remove_client(client)
            return False

        conn.settimeout(ACK_TIMEOUT)
        resp = _recv_json(conn)
        conn.settimeout(None)

        if resp is None:
            print(f"[server] 채팅 응답 실패 - client_idx={client.get('idx')}, addr={addr}")
            _remove_client(client)
            return False

        for line in resp.get("logs", []):
            print(f"[client idx({client.get('idx')})] {line}")

        if resp.get("status") == "ok":
            print(f"[server] 채팅 응답 수신 - client_idx={client.get('idx')}, status=ok, addr={addr}")
            return True

        print(f"[server] 채팅 응답 오류 - client_idx={client.get('idx')}, resp={resp}")
        return False


def _load_haste_check_config(direction: str) -> tuple[tuple[int, int], float, set[str]]:
    data = _load_macro_data()

    xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=direction)

    interval = float(data.get("haste_check_interval_seconds", HASTE_CHECK_DEFAULT_INTERVAL))
    if interval <= 0:
        interval = HASTE_CHECK_DEFAULT_INTERVAL

    direction_change_nicknames = _read_direction_change_nicknames(data)

    return xy, interval, direction_change_nicknames


def _load_same_front_nickname_chat_config() -> tuple[float, str, int | None, float]:
    data = _load_macro_data()

    try:
        seconds = float(data.get("same_front_nickname_chat_seconds", SAME_FRONT_NICKNAME_CHAT_DEFAULT_SECONDS))
    except (TypeError, ValueError):
        seconds = SAME_FRONT_NICKNAME_CHAT_DEFAULT_SECONDS

    try:
        cooldown = float(
            data.get(
                "same_front_nickname_chat_cooldown_seconds",
                SAME_FRONT_NICKNAME_CHAT_DEFAULT_COOLDOWN_SECONDS,
            )
        )
    except (TypeError, ValueError):
        cooldown = SAME_FRONT_NICKNAME_CHAT_DEFAULT_COOLDOWN_SECONDS

    message = str(data.get("same_front_nickname_client_message", "")).strip()
    client_idx_raw = data.get("same_front_nickname_chat_client_idx", 1)
    try:
        client_idx = int(client_idx_raw)
    except (TypeError, ValueError):
        client_idx = None

    return max(0.0, seconds), message, client_idx, max(0.0, cooldown)


def _load_low_mp_message_config() -> tuple[float, list[str]]:
    data = _load_macro_data()

    try:
        interval = float(
            data.get("low_mp_message_interval_seconds", LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS)
        )
    except (TypeError, ValueError):
        interval = LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS

    raw_messages = data.get("low_mp_messages", LOW_MP_MESSAGES_DEFAULT)
    if isinstance(raw_messages, str):
        messages = [raw_messages.strip()] if raw_messages.strip() else []
    elif isinstance(raw_messages, (list, tuple)):
        messages = [str(message).strip() for message in raw_messages if str(message).strip()]
    else:
        messages = []

    if not messages:
        messages = list(LOW_MP_MESSAGES_DEFAULT)

    return max(0.0, interval), messages


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
        if _sleep_interruptible(0.5):
            return last_value


def _clear_chat_input() -> bool:
    """Clear all visible chat input text, if any."""
    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
    input_text = macro.readInputText(img).strip()

    macro.arduino_key_down(win32con.VK_CONTROL)
    deadline = time.time() + 0.2
    while time.time() < deadline:
        macro.arduino_key_press(win32con.VK_BACK)
    macro.arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)

    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
    remaining_text = macro.readInputText(img).strip()
    if remaining_text:
        macro.arduino_key_down(win32con.VK_CONTROL)
        deadline = time.time() + 0.2
        while time.time() < deadline:
            macro.arduino_key_press(win32con.VK_BACK)
        macro.arduino_key_up(win32con.VK_CONTROL)
        time.sleep(0.1)
    return bool(input_text or remaining_text)


def _read_nickname_at_xy(check_xy: tuple[int, int]) -> str:
    x, y = check_xy
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    _clear_chat_input()
    macro.arduino_mouse_shift_click_right(x, y)
    time.sleep(0.15)

    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
    nickname = macro.readInputText(img).strip()
    _clear_chat_input()
    return nickname


def _turn_preferred_or_random(preferred_direction: str | None, excluded_direction: str) -> str | None:
    if preferred_direction:
        blocked_directions = _read_blocked_turn_directions(_load_macro_data())
        if (
            preferred_direction in TURN_DIRECTIONS
            and preferred_direction != excluded_direction
            and preferred_direction != macro.current_direction
            and preferred_direction not in blocked_directions
            and macro.turn_to(preferred_direction)
        ):
            return preferred_direction

    return macro.turn_random_excluding(excluded_direction)


def _try_haste_front_person(
    check_xy: tuple[int, int],
    direction_change_nicknames: set[str],
    preferred_turn_direction: str | None = None,
) -> tuple[str | None, str]:
    nickname = _read_nickname_at_xy(check_xy)
    if not nickname:
        print(f"[server] 헤이스트 확인 - nickname=없음, xy={check_xy}")
        return None, ""

    if nickname in direction_change_nicknames:
        direction = _turn_preferred_or_random(preferred_turn_direction, macro.low_count_direction)
        if direction is None:
            print(f"[server] 헤이스트 차단 - nickname='{nickname}', xy={check_xy}, turn=failed")
            return None, nickname
        print(f"[server] 헤이스트 차단 - nickname='{nickname}', xy={check_xy}, turn={direction}")
        return direction, nickname

    print(f"[server] 헤이스트 시도 - nickname='{nickname}', xy={check_xy}, key=F7")
    macro._arduino_send(f'KP,{win32con.VK_F7}')
    return "haste", nickname


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
    _last_shop_direction_force_time = time.time()
    base_shop_direction = macro.high_count_direction
    shop_direction = base_shop_direction
    preferred_turn_direction = "northeast" if base_shop_direction == "southeast" else None
    was_low_mp = False
    clients_snapshot = []
    prev_stage = None
    direction_synced = False
    same_front_nickname = None
    same_front_xy = None
    same_front_since = 0.0
    same_front_last_chat_key = None
    same_front_last_chat_time = 0.0
    exchange_window_nickname = None
    exchange_window_since = 0.0
    exchange_window_last_chat_key = None
    exchange_window_last_chat_time = 0.0
    low_mp_message_index = 0

    def reset_exchange_window_tracking() -> None:
        nonlocal exchange_window_nickname, exchange_window_since
        exchange_window_nickname = None
        exchange_window_since = 0.0

    def reset_trade_state() -> None:
        nonlocal stage, greeted_nickname, adena_before, prev_brightness, brightness_changed
        stage = WAIT_NICKNAME
        greeted_nickname = None
        adena_before = None
        prev_brightness = None
        brightness_changed = False
        reset_exchange_window_tracking()

    def turn_after_same_nickname_timeout(source: str, nickname: str, elapsed: float) -> bool:
        nonlocal shop_direction, _last_return_check_time, _last_shop_direction_force_time
        nonlocal same_front_nickname, same_front_xy, same_front_since

        direction = _turn_preferred_or_random(preferred_turn_direction, macro.low_count_direction)
        if direction is None:
            print(
                f"[server] 장사 방향 변경 실패 - source={source}, "
                f"nickname='{nickname}', elapsed={elapsed:.1f}s"
            )
            return False

        shop_direction = direction
        _last_return_check_time = time.time()
        _last_shop_direction_force_time = time.time()
        same_front_nickname = None
        same_front_xy = None
        same_front_since = 0.0
        print(
            f"[server] 장사 방향 변경 - source={source}, "
            f"nickname='{nickname}', elapsed={elapsed:.1f}s, direction={direction}"
        )
        return True

    def handle_exchange_window_timeout(nickname: str) -> bool:
        nonlocal exchange_window_nickname, exchange_window_since
        nonlocal exchange_window_last_chat_key, exchange_window_last_chat_time

        if not nickname:
            reset_exchange_window_tracking()
            return False

        now = time.time()
        if exchange_window_nickname == nickname:
            elapsed = now - exchange_window_since
        else:
            exchange_window_nickname = nickname
            exchange_window_since = now
            elapsed = 0.0

        chat_seconds, chat_message, chat_client_idx, chat_cooldown = _load_same_front_nickname_chat_config()
        if chat_seconds <= 0 or elapsed < chat_seconds:
            return False

        _add_direction_change_nickname(nickname)

        chat_key = ("exchange", nickname)

        print(f"[server] 거래창 유지 감지 - nickname='{nickname}', elapsed={elapsed:.1f}s, action=esc_and_client_chat")
        macro.key_press(win32con.VK_ESCAPE)
        if _sleep_interruptible(0.2):
            return True

        can_send_chat = (
            bool(chat_message)
            and (
                exchange_window_last_chat_key != chat_key
                or now - exchange_window_last_chat_time >= chat_cooldown
            )
        )
        if can_send_chat:
            try:
                rendered_message = chat_message.format(nickname=nickname)
            except (IndexError, KeyError, ValueError):
                rendered_message = chat_message

            chat_client = _select_chat_client(clients_snapshot, chat_client_idx)
            if chat_client is None:
                print("[server] 채팅 명령 스킵 - reason=no_connected_client, source=exchange_window")
            elif _send_client_chat(chat_client, rendered_message):
                exchange_window_last_chat_key = chat_key
                exchange_window_last_chat_time = now
                print(f"[server] 채팅 명령 완료 - client_idx={chat_client.get('idx')}, source=exchange_window")
        else:
            print("[server] 채팅 명령 스킵 - reason=cooldown_or_empty_message, source=exchange_window")

        turn_after_same_nickname_timeout("exchange_window", nickname, elapsed)
        reset_trade_state()
        _sleep_interruptible(0.5)
        return True

    def type_next_low_mp_message(force: bool = False) -> bool:
        nonlocal _last_type_string_time, low_mp_message_index

        interval, messages = _load_low_mp_message_config()
        now = time.time()
        if not force and now - _last_type_string_time < interval:
            return False

        message = messages[low_mp_message_index % len(messages)]
        low_mp_message_index += 1
        macro.arduino_type_string(message)
        _last_type_string_time = time.time()
        return True

    def cancel_invalid_trade_items(trade_items: dict[str, object]) -> bool:
        nonlocal _last_type_string_time

        occupied_slots = trade_items.get("occupied_slots", [])
        print(
            f"[server] 상대방 OK 후 아데나 외/빈 거래창 감지 -> Cancel, "
            f"trade_state={trade_items.get('state')}, occupied_slots={occupied_slots}"
        )
        macro.cancelExchange()
        if _sleep_interruptible(0.3):
            return True

        macro.force_set_foreground_window(macro.lineage1_hwnd)
        macro.arduino_type_string(INVALID_TRADE_ITEM_MESSAGE)
        _last_type_string_time = time.time()
        reset_trade_state()
        _sleep_interruptible(0.8)
        return True

    while running:
        if _request_f12_stop():
            break

        # 이전 stage가 READ_ADENA 이상이었을 경우 WAIT_NICKNAME 복귀 시 TAB + 타겟 리셋
        if stage != prev_stage:
            if stage == WAIT_NICKNAME and prev_stage is not None and prev_stage >= READ_ADENA:
                macro.key_press(win32con.VK_TAB)
                if _sleep_interruptible(0.3):
                    break
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
            if _clear_server_f10_slot_if_needed(img):
                img = macro.screenshot(hwnd=macro.lineage1_hwnd)
            try:
                _mp1 = macro.readMp(img)
            except macro.RestartButtonClicked:
                _request_restart_shutdown("server", click_server=False)
                break
            if _mp1 is not None:
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
                    if _sleep_interruptible(SAME_UNIT_DELAY - elapsed):
                        break
                if _try_use_potion(e):
                    _last_potion_idx_time[e["idx"]] = time.time()
                    if e["idx"] == 0 and "conn" in e:
                        if _sleep_interruptible(0.5):
                            break
                        macro.force_set_foreground_window(macro.lineage1_hwnd)
            if not running:
                break

            total_count = sum(e["available"] for e in clients_snapshot)
            should_face_low = total_count < macro.direction_threshold
            if time.time() - _last_status_print_time >= 3:
                for e in clients_snapshot:
                    print(f"[server] MP 상태 - idx={e['idx']}, mp={e['mp']}, available={e['available']}")
                status_xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=shop_direction)
                print(f"[server] 장사 상태 - direction={shop_direction}, xy={status_xy}")
                _last_status_print_time = time.time()

            if should_face_low:
                entering_low_mp = not was_low_mp
                was_low_mp = True
                sent_low_mp_message = False
                if macro.turn_to(macro.low_count_direction):
                    print(f"[server] 저MP 감지 -> {macro.low_count_direction}")
                if entering_low_mp:
                    sent_low_mp_message = type_next_low_mp_message(force=True)
                if not sent_low_mp_message:
                    type_next_low_mp_message()
                if _sleep_interruptible(0.5):
                    break
                continue
            else:
                if was_low_mp:
                    recover_direction = _choose_recovered_shop_direction(macro.high_count_direction)
                    if recover_direction is None:
                        if _sleep_interruptible(0.5):
                            break
                        continue

                    shop_direction = recover_direction
                    was_low_mp = False
                    _last_shop_direction_force_time = time.time()

                if macro.turn_to(shop_direction):
                    print(f"[server] 장사 방향 유지 -> {shop_direction}")
                    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
                    if _clear_server_f10_slot_if_needed(img):
                        img = macro.screenshot(hwnd=macro.lineage1_hwnd)

            if time.time() - _last_type_string_time >= 10:
                _ad_formats = [
                    macro.get_adena_price_notice(),
                    # "김남진 타쓰지 사기 조심!",
                    # "왜 나한테만 엄격한건데!",
                ]
                macro.arduino_type_string(random.choice(_ad_formats))
                _last_type_string_time = time.time()

            haste_check_xy, haste_check_interval, direction_change_nicknames = _load_haste_check_config(shop_direction)

            nickname = macro.readExchangeNickname(img=img)
            if nickname:
                if nickname in direction_change_nicknames:
                    print(f"[server] 지정 닉네임 거래창 감지: '{nickname}' -> ESC")
                    macro.key_press(win32con.VK_ESCAPE)
                    reset_exchange_window_tracking()
                    if _sleep_interruptible(0.5):
                        break
                    continue

                greeted_nickname = nickname
                # macro.arduino_type_string(f"\\f2{greeted_nickname}\\f7님 어서오세요!")
                stage = READ_ADENA
                continue

            if time.time() - _last_shop_direction_force_time >= SHOP_DIRECTION_FORCE_INTERVAL_SECONDS:
                if macro.turn_to(shop_direction, force=True):
                    print(f"[server] 장사 방향 주기 보정 -> {shop_direction}")
                    img = macro.screenshot(hwnd=macro.lineage1_hwnd)
                    if _clear_server_f10_slot_if_needed(img):
                        img = macro.screenshot(hwnd=macro.lineage1_hwnd)
                _last_shop_direction_force_time = time.time()
                if _sleep_interruptible(0.2):
                    break
                continue

            if (
                shop_direction != base_shop_direction
                and time.time() - _last_return_check_time >= DIRECTION_RETURN_CHECK_INTERVAL
            ):
                _last_return_check_time = time.time()
                base_check_xy = macro.get_configured_mouse_xy("server_mouse_x_y", direction=base_shop_direction)
                base_nickname = _read_nickname_at_xy(base_check_xy)

                if base_nickname in direction_change_nicknames:
                    print(
                        f"[server] 기본 방향 복귀 보류 - nickname='{base_nickname}', "
                        f"xy={base_check_xy}, current_direction={shop_direction}"
                    )
                    if _sleep_interruptible(0.5):
                        break
                    continue

                if macro.turn_to(base_shop_direction, force=True):
                    shop_direction = base_shop_direction
                    _last_shop_direction_force_time = time.time()
                    same_front_nickname = None
                    same_front_xy = None
                    same_front_since = 0.0
                    print(
                        f"[server] 기본 방향 복귀 - nickname='{base_nickname}', "
                        f"xy={base_check_xy}, direction={shop_direction}"
                    )
                else:
                    print(
                        f"[server] 기본 방향 복귀 실패 - nickname='{base_nickname}', "
                        f"xy={base_check_xy}, direction={base_shop_direction}"
                    )
                if _sleep_interruptible(0.5):
                    break
                continue

            if time.time() - _last_haste_check_time >= haste_check_interval:
                _last_haste_check_time = time.time()
                haste_result, front_nickname = _try_haste_front_person(
                    haste_check_xy,
                    direction_change_nicknames,
                    preferred_turn_direction,
                )
                if haste_result and haste_result != "haste":
                    shop_direction = haste_result
                    _last_return_check_time = time.time()
                    same_front_nickname = None
                    same_front_xy = None
                    same_front_since = 0.0
                    print(f"[server] 장사 방향 변경: {shop_direction}")

                if front_nickname and haste_result != "haste":
                    now = time.time()
                    _, chat_message, chat_client_idx, chat_cooldown = _load_same_front_nickname_chat_config()
                    chat_key = (front_nickname, haste_check_xy)
                    can_send_chat = (
                        bool(chat_message)
                        and (
                            same_front_last_chat_key != chat_key
                            or now - same_front_last_chat_time >= chat_cooldown
                        )
                    )

                    if can_send_chat:
                        try:
                            rendered_message = chat_message.format(nickname=front_nickname)
                        except (IndexError, KeyError, ValueError):
                            rendered_message = chat_message

                        chat_client = _select_chat_client(clients_snapshot, chat_client_idx)
                        if chat_client is None:
                            print("[server] blocked_front_nickname chat skipped: no connected client")
                        elif _send_client_chat(chat_client, rendered_message):
                            same_front_last_chat_key = chat_key
                            same_front_last_chat_time = now
                            print(
                                f"[server] blocked_front_nickname chat sent - "
                                f"client_idx={chat_client.get('idx')}, nickname='{front_nickname}'"
                            )
                    else:
                        print("[server] blocked_front_nickname chat skipped: cooldown or empty message")

                    same_front_nickname = None
                    same_front_xy = None
                    same_front_since = 0.0

                elif front_nickname and haste_result == "haste":
                    now = time.time()
                    if same_front_nickname == front_nickname and same_front_xy == haste_check_xy:
                        same_front_elapsed = now - same_front_since
                    else:
                        same_front_nickname = front_nickname
                        same_front_xy = haste_check_xy
                        same_front_since = now
                        same_front_elapsed = 0.0

                    chat_seconds, chat_message, chat_client_idx, chat_cooldown = _load_same_front_nickname_chat_config()
                    chat_key = (front_nickname, haste_check_xy)
                    same_front_timed_out = chat_seconds > 0 and same_front_elapsed >= chat_seconds
                    can_send_chat = (
                        same_front_timed_out
                        and chat_message
                        and (
                            same_front_last_chat_key != chat_key
                            or now - same_front_last_chat_time >= chat_cooldown
                        )
                    )
                    if same_front_timed_out:
                        _add_direction_change_nickname(front_nickname)

                    if can_send_chat:
                        try:
                            rendered_message = chat_message.format(nickname=front_nickname)
                        except (IndexError, KeyError, ValueError):
                            rendered_message = chat_message

                        chat_client = _select_chat_client(clients_snapshot, chat_client_idx)
                        if chat_client is None:
                            print("[server] 채팅 명령 스킵 - reason=no_connected_client, source=same_front_nickname")
                        elif _send_client_chat(chat_client, rendered_message):
                            same_front_last_chat_key = chat_key
                            same_front_last_chat_time = now
                            print(
                                f"[server] 채팅 명령 완료 - client_idx={chat_client.get('idx')}, "
                                f"source=same_front_nickname, nickname='{front_nickname}', "
                                f"elapsed={same_front_elapsed:.1f}s"
                            )
                    elif same_front_timed_out:
                        print("[server] 채팅 명령 스킵 - reason=cooldown_or_empty_message, source=same_front_nickname")

                    if same_front_timed_out:
                        turn_after_same_nickname_timeout(
                            "same_front_nickname",
                            front_nickname,
                            same_front_elapsed,
                        )
                elif haste_result != "haste":
                    same_front_nickname = None
                    same_front_xy = None
                    same_front_since = 0.0

                if haste_result:
                    if _sleep_interruptible(HASTE_AFTER_F7_WAIT_SECONDS):
                        break
                    continue

            if _sleep_interruptible(0.5):
                break

        # ── Stage 2: 교환 전 아데나 1회 측정 ────────────────────────────────
        elif stage == READ_ADENA:
            exchange_nickname = macro.readExchangeNickname(img)
            if not exchange_nickname:
                reset_exchange_window_tracking()
                stage = WAIT_NICKNAME
                continue
            _, _, direction_change_nicknames = _load_haste_check_config(shop_direction)
            if exchange_nickname in direction_change_nicknames:
                print(f"[server] 지정 닉네임 거래신청 차단: '{exchange_nickname}' -> ESC")
                macro.key_press(win32con.VK_ESCAPE)
                reset_exchange_window_tracking()
                stage = WAIT_NICKNAME
                if _sleep_interruptible(0.5):
                    break
                continue
            if handle_exchange_window_timeout(exchange_nickname):
                continue
            adena_before = macro.readAdena()
            macro._arduino_send(f'KP,{win32con.VK_F7}')
            stage = MONITOR_BRIGHTNESS

        # ── Stage 3: 슬롯 밝기 감시 → 임계값 초과 시 교환 수락 ─────────────
        elif stage == MONITOR_BRIGHTNESS:
            img = macro.screenshot()
            if _clear_server_f10_slot_if_needed(img):
                img = macro.screenshot()
            exchange_nickname = macro.readExchangeNickname(img)
            if not exchange_nickname:
                reset_exchange_window_tracking()
                stage = PICKUP
                continue
            if handle_exchange_window_timeout(exchange_nickname):
                continue

            slot = macro.crop(img, 258, 677, 30, 30)
            brightness = macro.get_brightness(slot)
            trade_items = macro.analyze_opponent_trade_items(img)
            trade_state = trade_items["state"]
            print(
                f"[server] 슬롯 밝기: {brightness:.2f}, "
                f"trade_state={trade_state}, occupied_slots={trade_items['occupied_slots']}"
            )

            if not brightness_changed and brightness > EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD:
                if trade_state == "adena_only":
                    if _sleep_interruptible(0.2):
                        break
                    confirm_img = macro.screenshot()
                    confirm_trade_items = macro.analyze_opponent_trade_items(confirm_img)
                    confirm_trade_state = confirm_trade_items["state"]
                    print(
                        f"[server] 거래 수락 직전 재확인: "
                        f"trade_state={confirm_trade_state}, "
                        f"occupied_slots={confirm_trade_items['occupied_slots']}"
                    )
                    if confirm_trade_state != "adena_only":
                        cancel_invalid_trade_items(confirm_trade_items)
                        continue
                    brightness_changed = True
                    macro.acceptExchange()
                else:
                    cancel_invalid_trade_items(trade_items)
                    continue
            prev_brightness = brightness
            if _sleep_interruptible(EXCHANGE_MONITOR_INTERVAL_SECONDS):
                break

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
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                macro.arduino_type_string("아데나를 받지 못 했습니다.")
                _last_type_string_time = time.time()
                if _sleep_interruptible(1.0):
                    break
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

            if received < macro.adena_per_pickup:
                print(f"[server] 아데나 부족: received={received}, required={macro.adena_per_pickup}")
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                macro.arduino_type_string(f"아데나가 부족합니다. 1방 {macro.adena_per_pickup}원입니다.")
                _last_type_string_time = time.time()
                if _sleep_interruptible(1.0):
                    break
                stage = WAIT_NICKNAME
                greeted_nickname = None
                adena_before = None
                prev_brightness = None
                brightness_changed = False
                continue

            pickup_count = macro.get_pickup_count_for_adena(received)

            # 핑 스레드의 concurrent 업데이트와 격리하기 위해 available을 별도 dict로 복사
            # clients_snapshot은 shallow copy라 핑 스레드가 동일 dict를 수정하므로,
            # pickup loop 전용 카운터를 따로 유지한다.
            pickup_avail: dict[int, int] = {id(c): c["available"] for c in clients_snapshot}
            total_available = sum(pickup_avail.values())
            remaining = min(macro.direction_threshold, pickup_count)
            successful_pickups = 0
            print(
                f"[server] 픽업 시작 - remaining={remaining}, "
                f"received={received}, available={total_available}"
            )

            # ── 픽업 분배 ───────────────────────────────────────────────────
            # 매 라운드: 전체 중 available 최댓값 탐색
            #   → 공유자 여럿이면 idx 내림차순 모두 실행
            #   → 혼자면 해당 client만 실행
            # 같은 idx는 SAME_UNIT_DELAY 이내 재전송 금지
            last_idx_time: dict = {}

            while remaining > 0 and running:
                if _request_f12_stop():
                    break
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
                        if _sleep_interruptible(SAME_UNIT_DELAY - elapsed):
                            break

                    pickup_skipped = False
                    if "conn" not in c:
                        print(f"[server] 픽업 실행 - target=server, remaining={remaining}")
                        ok = macro.pickup_lineage1(
                            target_nickname=greeted_nickname,
                            direction=shop_direction,
                            log_prefix="[server]",
                        )
                        if not ok:
                            print("[server] 픽업 스킵 - reason=target_failed, target=server")
                            pickup_skipped = True
                            _last_type_string_time = time.time()
                    else:
                        print(f"[server] 픽업 명령 전송 - target=client, idx={c['idx']}, remaining={remaining}")
                        pickup_status = _send_pickup(c, nickname=greeted_nickname, direction=shop_direction)
                        if pickup_status == "target_failed":
                            print(f"[server] 픽업 스킵 - reason=target_failed, target=client, idx={c['idx']}")
                            pickup_skipped = True
                            _last_type_string_time = time.time()
                        ok = pickup_status == "ok"

                    last_idx_time[c["idx"]] = time.time()
                    if ok or pickup_skipped:
                        remaining -= 1
                        pickup_avail[id(c)] -= 1
                        sent_any = True
                        if ok:
                            successful_pickups += 1

                if not sent_any:
                    if remaining > 0:
                        print(f"[server] 픽업 진행 중단 - reason=send_failed, remaining={remaining}")
                    break

            if not running:
                break

            if win32gui.GetForegroundWindow() != macro.lineage1_hwnd:
                macro.force_set_foreground_window(macro.lineage1_hwnd)
            if _sleep_interruptible(0.1):
                break
            if successful_pickups > 0:
                macro.arduino_type_string(f"감사합니다!")
                _last_type_string_time = time.time()
                if _sleep_interruptible(2.5):
                    break

            stage = WAIT_NICKNAME
            greeted_nickname = None
            adena_before = None
            prev_brightness = None
            brightness_changed = False


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    macro.init_setting("server")
    macro.start_restart_watcher(on_click=_handle_restart_watcher_click)

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
                _f12_stop_reported = False
                _restart_shutdown_started = False
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
