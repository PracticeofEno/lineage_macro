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
_MOUSE_DIR_PATH = os.path.join(_BASE, "mouse_direction_data.json")
_INDIVIDUAL_CFG_PATH = os.path.join(_BASE, "individual.json")

direction_threshold: int = 0
low_count_direction: str = "southeast"
high_count_direction: str = "northwest"

WAIT_NICKNAME, READ_ADENA, MONITOR_BRIGHTNESS, PICKUP = range(4)


def _load_mouse_dir() -> dict:
    with open(_MOUSE_DIR_PATH, encoding="utf-8") as f:
        return json.load(f)


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
    data = _load_mouse_dir()
    return tuple(data["mouse_x_y_by_direction"][char["direction"]])


def _change_direction(char: dict, direction: str):
    with _fg_lock:
        _set_context(char)
        macro.force_set_foreground_window(char["hwnd"])
        macro._DIRECTION_FUNCS[direction]()
    char["direction"] = direction


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
    global direction_threshold
    img = macro.screenshot(hwnd=char["hwnd"])
    mp = macro.readMp(img)
    if mp != 0:
        char["mp"] = mp
        if direction_threshold == 0:
            direction_threshold = int(mp // 20)
    char["available"] = int(char["mp"] // 20)


def _manage_direction(char: dict):
    if char["available"] < direction_threshold:
        if char["direction"] != low_count_direction:
            _change_direction(char, low_count_direction)
            time.sleep(1)
    else:
        if char["direction"] != high_count_direction:
            time.sleep(1)
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
    }


def _reset_state(state: dict):
    state["stage"] = WAIT_NICKNAME
    state["greeted_nickname"] = None
    state["adena_before"] = None
    state["prev_brightness"] = None
    state["brightness_changed"] = False


def _step_char(char: dict, state: dict, label: str):
    stage = state["stage"]

    # ── Stage 1: 광고 / 닉네임 대기 ─────────────────────────────────────────
    if stage == WAIT_NICKNAME:
        if time.time() - state["last_ad_time"] >= 12:
            a = char["available"]
            _ad_formats = [
                f"헤이 {macro.adena_per_pickup} {a}방 !",
                f"{a}방 가능 한방에 {macro.adena_per_pickup}아데나!",
            ]
            with _fg_lock:
                macro.force_set_foreground_window(char["hwnd"])
            macro.arduino_type_string(random.choice(_ad_formats))
            state["last_ad_time"] = time.time()

        _set_context(char)
        img = macro.screenshot(hwnd=char["hwnd"])
        nickname = macro.readExchangeNickname(img=img)
        if nickname:
            state["greeted_nickname"] = nickname
            state["stage"] = READ_ADENA
            return

        with _fg_lock:
            macro.force_set_foreground_window(char["hwnd"])
        if macro.has_target_in_input():
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

        _set_context(char)
        adena_after = macro.readAdena()
        received = adena_after - state["adena_before"]
        print(f"[{label}] 아데나 변화 감지: {state['adena_before']} → {adena_after}, received: {received}")

        if char["available"] > 0:
            print(f"[{label}] 픽업 실행")
            _pickup(char, state["greeted_nickname"])
            char["available"] -= 1

        with _fg_lock:
            _set_context(char)
            macro.force_set_foreground_window(char["hwnd"])
        time.sleep(0.1)
        if received > 0:
            display_name = state["greeted_nickname"][:2] if len(state["greeted_nickname"]) > 2 else state["greeted_nickname"]
            macro.arduino_type_string(f"{display_name}님 감사합니당~!")

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
    low_count_direction = _cfg["low_count_direction"]
    high_count_direction = _cfg["high_count_direction"]
    init_direction = _cfg["current_direction"]
    print(init_direction)

    server_char = {"hwnd": _find_hwnd("server"), "mp": 0, "available": 0, "direction": init_direction}
    client_char = {"hwnd": _find_hwnd("client"), "mp": 0, "available": 0, "direction": init_direction}

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
