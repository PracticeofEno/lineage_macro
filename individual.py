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

import macro

_fg_lock = threading.Lock()
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

WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)


def _find_hwnd(title: str) -> int:
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    if not result:
        raise RuntimeError(f"'{title}' 윈도우를 찾을 수 없습니다.")
    return result[0]


def _set_context(char: dict):
    macro.lineage1_hwnd = char["hwnd"]
    macro.current_direction = char["direction"]


def _pickup_xy(char: dict) -> tuple[int, int]:
    return tuple(_cfg["mouse_x_y_by_direction"][char["direction"]])


def _change_direction(char: dict, direction: str):
    with _fg_lock:
        _set_context(char)
        macro.force_set_foreground_window(char["hwnd"])
        x, y = _cfg["turn_x_y_by_direction"][direction]
        macro.arduino_mouse_shift_click_left(x, y)
    char["direction"] = direction


def _change_to_random_direction(char: dict):
    directions = [d for d in _cfg["turn_x_y_by_direction"] if d != char["direction"]]
    _change_direction(char, random.choice(directions))


def _is_blocked_text(text: str) -> bool:
    blocked_list = _cfg.get("blocked_list", [])
    return text.strip() in blocked_list


def _pickup(char: dict, nickname: str | None):
    with _fg_lock:
        _set_context(char)
        x, y = _pickup_xy(char)
        macro.force_set_foreground_window(char["hwnd"])
        win32api.SetCursorPos((x, y))
        time.sleep(0.1)
        for attempt in range(4):
            macro.arduino_mouse_shift_click_right(x, y)
            time.sleep(0.1)
            img = macro.screenshot(hwnd=char["hwnd"])
            input_text = macro.readInputText(img)
            print(f"[macro] 타겟 확인 ({attempt+1}/4): '{input_text}' == '{nickname}'?")
            macro.arduino_key_down(win32con.VK_CONTROL)
            macro.arduino_key_press(win32con.VK_BACK)
            macro.arduino_key_up(win32con.VK_CONTROL)
            time.sleep(0.1)
            if input_text == nickname:
                print("[macro] 타겟 고정 성공")
                break
        else:
            print("[macro] 타겟 고정 실패 - pickup 진행")
        macro.key_press(win32con.VK_F5)
        time.sleep(0.1)
        macro.mouse_click_left(x, y)
        time.sleep(0.1)


def _update_mp(char: dict):
    img = macro.screenshot(hwnd=char["hwnd"])
    mp = macro.readMp(img)
    if mp != 0:
        char["mp"] = mp
        if char["direction_threshold"] == 0:
            char["direction_threshold"] = int(mp // 20)
    char["available"] = int(char["mp"] // 20)


def _try_use_potion(char: dict, label: str) -> bool:
    if char["available"] >= 2:
        return False

    now = time.time()
    if now - char.get("potion_last_used", 0.0) < POTION_COOLDOWN:
        return False

    with _fg_lock:
        _set_context(char)
        macro.use_potion()
    char["potion_last_used"] = now
    print(f"[{label}] 포션 사용 (available: {char['available']})")
    return True


def _manage_direction(char: dict):
    if char["direction_initialized"]:
        return
    if char["direction"] != high_count_direction:
        _change_direction(char, high_count_direction)
        time.sleep(1)
    char["direction_initialized"] = True


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
    }


def _reset_state(state: dict):
    state["stage"] = WAIT_NICKNAME
    state["greeted_nickname"] = None
    state["adena_before"] = None
    state["prev_brightness"] = None
    state["brightness_changed"] = False
    state["pickups_remaining"] = None
    state["available_at_exchange"] = None


def _step_char(char: dict, state: dict, label: str):
    stage = state["stage"]

    # ── Stage 1: 광고 / 닉네임 대기 ─────────────────────────────────────────
    if stage == WAIT_NICKNAME:
        if time.time() - state["last_ad_time"] >= 12:
            a = char["available"]
            with _fg_lock:
                macro.force_set_foreground_window(char["hwnd"])
            if a < 3:
                macro.arduino_type_string(f"엠탐.. {char['available']}/3")
                # _low_mp_formats = [
                    # f"엠탐.. {char['available']}/3",
                    # f"잠시만요.. {char['available']}/3",
                # ]
                # macro.arduino_type_string(f"엠탐.. {char['available']}/3")
                # state["low_mp_ad_idx"] += 1
            else:
                _ad_formats = [
                    f"헤이 {adena_per_pickup} {a}방 가능!",
                ]
                macro.arduino_type_string(random.choice(_ad_formats))
            state["last_ad_time"] = time.time()

        _set_context(char)
        img = macro.screenshot(hwnd=char["hwnd"])
        nickname = macro.readExchangeNickname(img=img)
        if nickname:
            if _is_blocked_text(nickname):
                print(f"[{label}] blocked_list exchange nickname: '{nickname}' -> reject")
                with _fg_lock:
                    _set_context(char)
                    macro.force_set_foreground_window(char["hwnd"])
                    macro.rejectExchange()
                _reset_state(state)
                return
            state["greeted_nickname"] = nickname
            state["stage"] = READ_ADENA
            return

        with _fg_lock:
            macro.force_set_foreground_window(char["hwnd"])
        has_target, input_text = macro.has_target_in_input(
            _pickup_xy(char),
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
                _change_to_random_direction(char)
                state["last_input_text"] = None
                state["input_text_since"] = time.time()
            macro._arduino_send(f'KP,{win32con.VK_F7}')

    # ── Stage 2: 교환 전 아데나 1회 측정 ─────────────────────────────────────
    elif stage == READ_ADENA:
        _set_context(char)
        img = macro.screenshot(hwnd=char["hwnd"])
        if not macro.readExchangeNickname(img):
            _reset_state(state)
            return
        state["adena_before"] = macro.readAdena()
        macro._arduino_send(f'KP,{win32con.VK_F7}')
        state["stage"] = MONITOR_BRIGHTNESS

    # ── Stage 3: 슬롯 밝기 감시 → 변화 시 교환 수락 ─────────────────────────
    elif stage == MONITOR_BRIGHTNESS:
        _set_context(char)
        img = macro.screenshot()
        if not macro.readExchangeNickname(img):
            state["stage"] = PICKUP
            return

        slot = macro.crop(img, 258, 677, 30, 30)
        brightness = macro.get_brightness(slot)
        print(f"[{label}] 슬롯 밝기: {brightness:.2f}")

        if (state["prev_brightness"] is not None) and (brightness != state["prev_brightness"] or brightness > 110):
            state["brightness_changed"] = True
            if state["available_at_exchange"] is None:
                state["available_at_exchange"] = int(char["available"])
            with _fg_lock:
                macro.force_set_foreground_window(char["hwnd"])
            macro.acceptExchange()
        state["prev_brightness"] = brightness

    # ── Stage 4: 픽업 ────────────────────────────────────────────────────────
    elif stage == PICKUP:
        if not state["brightness_changed"]:
            _set_context(char)
            macro.key_press(win32con.VK_TAB)
            time.sleep(0.3)
            _reset_state(state)
            return

        # 최초 진입: 아데나 측정 후 지급 횟수 계산
        if state["pickups_remaining"] is None:
            _set_context(char)
            adena_after = macro.readAdena()
            received = adena_after - state["adena_before"]
            paid_pickups = max(0, int(received // adena_per_pickup))
            available_at_exchange = int(state["available_at_exchange"])
            state["pickups_remaining"] = min(paid_pickups, available_at_exchange)
            print(f"[{label}] received: {received}, pickups_remaining: {state['pickups_remaining']}")

        # 픽업 1회 실행 후 return → 상대방 차례 양보
        if state["pickups_remaining"] > 0:
            print(f"[{label}] 픽업 실행 (남은: {state['pickups_remaining']})")
            _pickup(char, state["greeted_nickname"])
            state["pickups_remaining"] -= 1
            return

        # 픽업 완료
        with _fg_lock:
            _set_context(char)
            macro.force_set_foreground_window(char["hwnd"])
        time.sleep(0.1)
        macro.arduino_type_string(f"감삼당~")
        macro.key_press(win32con.VK_TAB)
        time.sleep(0.3)
        _reset_state(state)


def exchange_loop():
    global running

    server_state = _make_state()
    client_state = _make_state()
    _last_status_print_time = 0.0

    while running:
        _update_mp(server_char)
        _update_mp(client_char)

        if server_state["stage"] == WAIT_NICKNAME:
            _try_use_potion(server_char, "server")
        if client_state["stage"] == WAIT_NICKNAME:
            _try_use_potion(client_char, "client")

        if time.time() - _last_status_print_time >= 3:
            print(f"[server] MP: {server_char['mp']}, 잔여: {server_char['available']}, stage: {server_state['stage']}")
            print(f"[client] MP: {client_char['mp']}, 잔여: {client_char['available']}, stage: {client_state['stage']}")
            _last_status_print_time = time.time()

        _manage_direction(server_char)
        _manage_direction(client_char)

        _step_char(server_char, server_state, "server")
        _step_char(client_char, client_state, "client")

        time.sleep(0.5)


if __name__ == "__main__":
    macro.init_setting("server")
    with open(_INDIVIDUAL_CFG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)
    adena_per_pickup = _cfg["adena_per_pickup"]
    low_count_direction = _cfg["low_count_direction"]
    high_count_direction = _cfg["high_count_direction"]
    init_direction = _cfg["current_direction"]
    print(f"direction: {init_direction}, low: {low_count_direction}, high: {high_count_direction}")

    server_char = {"hwnd": _find_hwnd("server"), "mp": 0, "available": 0, "direction": init_direction, "direction_threshold": 0,  "direction_initialized": False, "potion_last_used": 0.0}
    client_char = {"hwnd": _find_hwnd("client"), "mp": 0, "available": 0, "direction": init_direction, "direction_threshold": 0, "direction_initialized": False, "potion_last_used": 0.0}

    print("\n명령어: q=종료, 1=exchange 시작, 2=exchange 중지")
    exchange_thread = None

    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            running = False
            break
        if cmd == "1":
            if exchange_thread and exchange_thread.is_alive():
                print("[individual] exchange 이미 실행 중")
            else:
                with _fg_lock:
                    macro.force_set_foreground_window(server_char["hwnd"])
                running = True
                exchange_thread = threading.Thread(target=exchange_loop, daemon=True)
                exchange_thread.start()
        if cmd == "2":
            running = False
