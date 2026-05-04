"""
individual.py - server/client 두 윈도우 단일 프로세스 Exchange
  - 하나의 exchange_loop에서 server/client 각자의 state machine을 처리
  - available/방향/광고/교환 모두 각자 독립 동작
  - 픽업은 각자 1번씩
  - 방향/마우스 좌표는 mouse_direction_data.json 참조
"""

import json
import os
import time
import random
import threading
import win32api
import win32con
import win32gui

from macro import _arduino_send
import utility

running = True

server_char: dict = {}
client_char: dict = {}

_BASE = os.path.dirname(os.path.abspath(__file__))
_INDIVIDUAL_CFG_PATH = os.path.join(_BASE, "individual.json")

_cfg: dict = {}

adena_per_pickup: int = 150
low_count_direction: str = "southeast"
high_count_direction: str = "northwest"
POTION_COOLDOWN = 600
PREFERRED_RANDOM_TURN_DIRECTIONS = {"northeast", "southeast", "southwest", "northwest"}
PREFERRED_RANDOM_TURN_WEIGHT = 0.75
NON_PREFERRED_TURN_DELAY = 60.0

WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)

def _pickup_xy(char: dict) -> tuple[int, int]:
    return tuple(_cfg["mouse_x_y_by_direction"][char["direction"]])

def use_potion():
    _arduino_send(f'KP,{win32con.VK_F8}')

def rejectExchange():
    win32api.SetCursorPos((311, 752))
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    utility.arduino_key_press(ord('Y'))
    time.sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')
    time.sleep(0.3)

def acceptExchange():
    win32api.SetCursorPos((247, 752))
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    utility.arduino_key_press(ord('Y'))
    time.sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')
    time.sleep(0.3)

def _change_direction(char: dict, direction: str):
    utility.set_forground_window(char["hwnd"])
    x, y = _cfg["turn_x_y_by_direction"][direction]
    utility.arduino_mouse_shift_click_left(x, y)
    char["direction"] = direction


def _change_to_random_direction(char: dict):
    directions = [d for d in _cfg["turn_x_y_by_direction"] if d != char["direction"]]
    preferred = [d for d in directions if d in PREFERRED_RANDOM_TURN_DIRECTIONS]
    remaining = [d for d in directions if d not in PREFERRED_RANDOM_TURN_DIRECTIONS]

    if preferred and remaining:
        directions = preferred if random.random() < PREFERRED_RANDOM_TURN_WEIGHT else remaining
    else:
        directions = preferred or remaining

    if directions:
        _change_direction(char, random.choice(directions))


def _change_to_preferred_random_direction(char: dict):
    directions = [
        d
        for d in _cfg["turn_x_y_by_direction"]
        if d in PREFERRED_RANDOM_TURN_DIRECTIONS and d != char["direction"]
    ]
    if directions:
        _change_direction(char, random.choice(directions))


def _is_blocked_text(text: str) -> bool:
    blocked_list = _cfg.get("blocked_list", [])
    return text.strip() in blocked_list


def _add_blocked_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    blocked_list = _cfg.setdefault("blocked_list", [])
    if text in blocked_list:
        return False
    blocked_list.append(text)
    return True


def _pickup(char: dict, nickname: str | None):
    x, y = _pickup_xy(char)
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)
    for attempt in range(4):
        utility.arduino_mouse_shift_click_right(x, y)
        time.sleep(0.1)
        img = utility.screenshot(hwnd=char["hwnd"])
        input_text = utility.readInputText(img)
        print(f"[macro] 타겟 확인 ({attempt+1}/4): '{input_text}' == '{nickname}'?")
        utility.arduino_key_down(win32con.VK_CONTROL)
        utility.arduino_key_press(win32con.VK_BACK)
        utility.arduino_key_press(win32con.VK_BACK)
        utility.arduino_key_up(win32con.VK_CONTROL)
        time.sleep(0.1)
        if input_text == nickname:
            print("[macro] 타겟 고정 성공")
            break
    else:
        print("[macro] 타겟 고정 실패 - pickup 진행")
    utility.arduino_key_press(win32con.VK_F5)
    time.sleep(0.1)
    utility.arduino_mouse_click_left(x, y)
    time.sleep(0.1)


def _update_mp(char: dict, img=None):
    mp = utility.readMp(img)
    if mp != 0:
        char["mp"] = mp
        if char["direction_threshold"] == 0:
            char["direction_threshold"] = int(mp // 20)
    char["available"] = int(mp // 20)


def _try_use_potion(char: dict, label: str) -> bool:
    if char["available"] >= 2:
        return False

    now = time.time()
    if now - char.get("potion_last_used", 0.0) < POTION_COOLDOWN:
        return False

    utility.use_potion()
    char["potion_last_used"] = now
    print(f"[{label}] 포션 사용 (available: {char['available']})")
    return True

def has_target_in_input(
    xy: tuple[int, int] | None = None,
    *,
    hwnd,
    label: str | None = None,
    return_text: bool = False,
) -> bool | tuple[bool, str]:
    """shift+우클릭으로 타겟 확인 후 입력창 초기화, 타겟이 있으면 True 반환"""
    global _pickup_xy
    if xy is None and _pickup_xy is None:
        data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "individual.json")
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        _pickup_xy = tuple(data["mouse_x_y"])
    x, y = tuple(xy) if xy is not None else _pickup_xy
    win32api.SetCursorPos((x, y))
    utility.arduino_mouse_shift_click_right(x, y)
    time.sleep(0.1)
    img = utility.screenshot(hwnd=hwnd)
    input_text = utility.readInputText(img)
    utility.arduino_key_down(win32con.VK_CONTROL)
    utility.arduino_key_press(win32con.VK_BACK)
    utility.arduino_key_press(win32con.VK_BACK)
    utility.arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)
    has_target = bool(input_text)
    if return_text:
        return has_target, input_text
    return has_target

def _manage_direction(char: dict):
    if char["direction_initialized"]:
        return
    _restore_high_count_direction(char)
    char["direction_initialized"] = True


def _restore_high_count_direction(char: dict):
    if char["direction"] == high_count_direction:
        return
    _change_direction(char, high_count_direction)
    time.sleep(1)


def _make_state() -> dict:
    return {
        "stage": WAIT_NICKNAME,
        "greeted_nickname": None,
        "adena_before": None,
        "prev_brightness": None,
        "brightness_changed": False,
        "last_ad_time": 0.0,
        "last_input_text": None,
        "input_text_since": 0.0,
        "pickups_remaining": None,
        "available_at_exchange": None,
        "low_mp_ad_idx": 0,
        "non_preferred_direction_since": 0.0,
        "last_target_check": 0.0,
        "last_status_print_time": 0.0,
    }


def _reset_state(state: dict):
    state["stage"] = WAIT_NICKNAME
    state["greeted_nickname"] = None
    state["adena_before"] = None
    state["prev_brightness"] = None
    state["brightness_changed"] = False
    state["pickups_remaining"] = None
    state["available_at_exchange"] = None
    state["non_preferred_direction_since"] = 0.0


def _type_chat(char: dict, text: str):
    utility.arduino_type_string(text)


def _read_exchange_nickname(char: dict, img) -> str:
    if char.get("exchange_nickname_xy") is None:
        xy = utility.findExchangeNicknameY(img)
        if xy is None:
            return ''
        char["exchange_nickname_xy"] = xy
    _, y = char["exchange_nickname_xy"]
    return utility.read_exchange_nickname_img(img, y)


def _step_char(char: dict, state: dict, label: str) -> bool:
    utility.set_forground_window(char["hwnd"])
    stage = state["stage"]
    # ── Stage 1: 광고 / 닉네임 대기 ─────────────────────────────────────────
    if stage == WAIT_NICKNAME:
        img = utility.screenshot(hwnd=char["hwnd"])
        _update_mp(char, img)
        _try_use_potion(char, label)
        if time.time() - state["last_status_print_time"] >= 3:
            print(f"[{label}] MP: {char['mp']}, 잔여: {char['available']}, stage: {state['stage']}")
            state["last_status_print_time"] = time.time()
            
        now = time.time()
        if char["direction"] in PREFERRED_RANDOM_TURN_DIRECTIONS:
            state["non_preferred_direction_since"] = 0.0
        elif state["non_preferred_direction_since"] == 0.0:
            state["non_preferred_direction_since"] = now
        elif now - state["non_preferred_direction_since"] >= NON_PREFERRED_TURN_DELAY:
            _change_to_preferred_random_direction(char)
            state["non_preferred_direction_since"] = 0.0

        if time.time() - state["last_ad_time"] >= 12:
            a = char["available"]
            if a < 3:
                _type_chat(char, f"엠탐.. {char['available']}/3")
            else:
                _ad_formats = [
                    f"헤이 {adena_per_pickup} {a}방 가능!",
                ]
                _type_chat(char, random.choice(_ad_formats))
            state["last_ad_time"] = time.time()

        nickname = _read_exchange_nickname(char, img)
        if nickname:
            state["greeted_nickname"] = nickname
            state["stage"] = READ_ADENA
            return

        has_target, input_text = has_target_in_input(
            _pickup_xy(char),
            hwnd=char["hwnd"],
            label=label,
            return_text=True,
        )
        if has_target:
            print(f"[{label}] 타겟 감지된 입력창 텍스트: '{input_text}'")
            if _is_blocked_text(input_text):
                print(f"[{label}] blocked_list 대상 감지: '{input_text}' -> 방향 전환")
                _change_to_random_direction(char)
                state["last_input_text"] = None
                state["input_text_since"] = time.time()
                return
            if input_text != state["last_input_text"]:
                state["last_input_text"] = input_text
                state["input_text_since"] = time.time()
            elif time.time() - state["input_text_since"] >= 60:
                if _add_blocked_text(input_text):
                    print(f"[{label}] 60s same input_text -> blocked_list add: '{input_text}'")
                _change_to_random_direction(char)
                state["last_input_text"] = None
                state["input_text_since"] = time.time()
            utility.arduino_key_press(win32con.VK_F7)
        else:
            state["last_input_text"] = None
            state["input_text_since"] = 0.0

    # ── Stage 2: 교환 전 아데나 1회 측정 ─────────────────────────────────────
    elif stage == READ_ADENA:
        img = utility.screenshot(hwnd=char["hwnd"])
        if not _read_exchange_nickname(char, img):
            _reset_state(state)
            return
        state["adena_before"] = utility.readAdena(hwnd=char["hwnd"])
        utility.arduino_key_press(win32con.VK_F7)
        state["stage"] = MONITOR_BRIGHTNESS

    # ── Stage 3: 슬롯 밝기 감시 → 변화 시 교환 수락 ─────────────────────────
    elif stage == MONITOR_BRIGHTNESS:
        img = utility.screenshot(hwnd=char["hwnd"])
        nickname = _read_exchange_nickname(char, img)
        if not nickname:
            state["stage"] = PICKUP
            return

        slot = utility.crop(img, 258, 677, 30, 30)
        brightness = utility.get_brightness(slot)
        print(f"[{label}] 슬롯 밝기: {brightness:.2f}")

        has_prev_brightness = state["prev_brightness"] is not None
        exchange_nickname = state["greeted_nickname"] or nickname
        if has_prev_brightness and _is_blocked_text(exchange_nickname):
            print(f"[{label}] blocked_list exchange nickname: '{exchange_nickname}' -> reject")
            rejectExchange()
            time.sleep(0.3)
            _reset_state(state)
            return

        if has_prev_brightness and (brightness != state["prev_brightness"] or brightness > 105):
            state["brightness_changed"] = True
            if state["available_at_exchange"] is None:
                state["available_at_exchange"] = int(char["available"])
            acceptExchange()
        state["prev_brightness"] = brightness

    # ── Stage 4: 픽업 ────────────────────────────────────────────────────────
    elif stage == PICKUP:
        if not state["brightness_changed"]:
            time.sleep(0.1)
            _restore_high_count_direction(char)
            _reset_state(state)
            return

        # 최초 진입: 아데나 측정 후 지급 횟수 계산
        if state["pickups_remaining"] is None:
            adena_after = utility.readAdena(hwnd=char["hwnd"])
            received = adena_after - state["adena_before"]
            paid_pickups = max(0, int(received // adena_per_pickup))
            available_at_exchange = int(state["available_at_exchange"])
            state["pickups_remaining"] = min(paid_pickups, available_at_exchange)
            print(f"[{label}] received: {received}, pickups_remaining: {state['pickups_remaining']}")

        # 픽업 1회 실행 후 return → 상대방 차례 양보
        if state["pickups_remaining"] > 0:
            if time.time() - char.get("last_pickup_time", 0.0) < 0.5:
                return
            print(f"[{label}] 픽업 실행 (남은: {state['pickups_remaining']})")
            _pickup(char, state["greeted_nickname"])
            char["last_pickup_time"] = time.time()
            state["pickups_remaining"] -= 1
            return

        # 픽업 완료
        time.sleep(0.1)
        _type_chat(char, f"감삼당~")
        time.sleep(0.1)
        _restore_high_count_direction(char)
        _reset_state(state)
        return True


def exchange_loop():
    global running

    server_state = _make_state()
    client_state = _make_state()

    while running:

        _manage_direction(server_char)
        _manage_direction(client_char)

        _step_char(server_char, server_state, "server")
        _step_char(client_char, client_state, "client")
        time.sleep(0.1)


if __name__ == "__main__":
    with open(_INDIVIDUAL_CFG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)
    adena_per_pickup = _cfg["adena_per_pickup"]
    low_count_direction = _cfg["low_count_direction"]
    high_count_direction = _cfg["high_count_direction"]
    init_direction = _cfg["current_direction"]
    print(f"direction: {init_direction}, low: {low_count_direction}, high: {high_count_direction}")

    server_char = {"hwnd": utility.find_hwnd("server"), "mp": 0, "available": 0, "direction": init_direction, "direction_threshold": 0,  "direction_initialized": False, "potion_last_used": 0.0, "last_pickup_time": 0.0, "exchange_nickname_xy": None}
    client_char = {"hwnd": utility.find_hwnd("client"), "mp": 0, "available": 0, "direction": init_direction, "direction_threshold": 0, "direction_initialized": False, "potion_last_used": 0.0, "last_pickup_time": 0.0, "exchange_nickname_xy": None}

    print("\n명령어: q=종료, 1=exchange 시작, 2=exchange 중지")
    exchange_thread = None

    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            break
        if cmd == "1":
            utility.set_forground_window(server_char["hwnd"])
            if exchange_thread and exchange_thread.is_alive():
                print("[individual] exchange 이미 실행 중")
            else:
                running = True
                exchange_thread = threading.Thread(target=exchange_loop, daemon=True)
                exchange_thread.start()
        if cmd == "2":
            running = False
