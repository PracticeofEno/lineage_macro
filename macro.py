from itertools import count
import os
import sys
import json
import random
import win32api
import win32com
import win32con
import win32gui
import win32process
import win32ui
import time
import socket as _socket
import threading as _threading
import numpy as np
from ctypes import windll
from datetime import datetime
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hangul

_BASE = os.path.dirname(os.path.abspath(__file__))
_CONVERTED_DATA_PATH = os.path.join(_BASE, "converted_data.json")
_TARGET_CHECK_FAILURES_PATH = os.path.join(_BASE, "target_check_failures.json")
with open(_CONVERTED_DATA_PATH, encoding="utf-8") as _f:
    _converted_map: dict[str, str] = json.load(_f)


def lookup(coord_string: str) -> str | None:
    return _converted_map.get(coord_string)


def image_to_coord_string(image: Image.Image, color: tuple) -> str:
    arr = np.array(image.convert("RGB"))
    r, g, b = color
    mask = (arr[:,:,0] == r) & (arr[:,:,1] == g) & (arr[:,:,2] == b)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)


def crop(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    return image.crop((x, y, x + width, y + height))


def read_text(image: Image.Image, x: int, y: int, color: tuple) -> str:
    result = []
    img_width = image.width
    while x < img_width:
        matched = None
        matched_width = None
        for w in (10, 20):
            if x + w > img_width:
                continue
            s = image_to_coord_string(crop(image, x, y, w, 24), color)
            if lookup(s) is not None:
                matched = lookup(s)
                matched_width = w
                break
        if matched is None:
            break
        result.append(matched)
        x += matched_width
    return ''.join(result)


def read_line(image: Image.Image, x: int, y: int, color: tuple) -> str:
    text = read_text(image, x, y, color)
    if not text:
        text = read_text(image, x + 10, y, color)
    return text


def _read_exchange_nickname_img(screenshot: Image.Image, y: int = 292) -> str:
    x = 107
    w, h = 140, 24
    color = (255, 255, 255)
    best = ''
    while x >= 57:
        cropped = crop(screenshot, x, y, w, h)
        text = read_text(cropped, 0, 0, color)
        if len(text) > len(best):
            best = text
        x -= 5
    return best

lineage1_hwnd = None

# ── Arduino Proxy 연결 ────────────────────────────────────────────────────────
# arduino_proxy.py 가 127.0.0.1:9998 에서 실행 중이어야 한다.
_PROXY_HOST = '127.0.0.1'
_PROXY_PORT = 9998
_proxy_conn: _socket.socket | None = None
_proxy_lock = _threading.Lock()
_screenshot_lock = _threading.Lock()


def _proxy_connect():
    global _proxy_conn
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.connect((_PROXY_HOST, _PROXY_PORT))
    _proxy_conn = s
    print(f"[macro] Arduino proxy 연결됨: {_PROXY_HOST}:{_PROXY_PORT}")


# ── Arduino HID 래퍼 ──────────────────────────────────────────────────────────
# 기존 winapi 함수(key_down / key_up / mouse_click_left 등)와 동일한 인터페이스.
# Python 쪽은 Windows VK 코드를 그대로 넘기면 Arduino 가 HID 코드로 변환한다.

def _arduino_send(cmd: str) -> str:
    """명령을 proxy 에 전송하고 Arduino 의 응답을 반환한다."""
    global _proxy_conn
    with _proxy_lock:
        if _proxy_conn is None:
            _proxy_connect()
        try:
            _proxy_conn.sendall((cmd + '\n').encode())
            buf = b''
            while b'\n' not in buf:
                chunk = _proxy_conn.recv(256)
                if not chunk:
                    raise OSError("proxy 연결 끊김")
                buf += chunk
            return buf.split(b'\n')[0].decode().strip()
        except OSError:
            # 재연결 한 번 시도
            try:
                _proxy_conn.close()
            except OSError:
                pass
            _proxy_conn = None
            _proxy_connect()
            _proxy_conn.sendall((cmd + '\n').encode())
            buf = b''
            while b'\n' not in buf:
                chunk = _proxy_conn.recv(256)
                if not chunk:
                    raise OSError("proxy 재연결 후에도 응답 없음")
                buf += chunk
            return buf.split(b'\n')[0].decode().strip()


def arduino_key_down(vk: int):
    _arduino_send(f'KD,{vk}')


def arduino_key_up(vk: int):
    _arduino_send(f'KU,{vk}')


def arduino_key_press(vk: int, duration: float = 0.05):
    """duration 이 필요 없는 경우 Arduino 내부에서 30 ms 딜레이를 처리한다."""
    _arduino_send(f'KP,{vk}')
    if duration > 0.05:
        time.sleep(duration - 0.05)


def arduino_mouse_move(x: int, y: int):
    _arduino_send(f'MM,{x},{y}')


def arduino_mouse_click_left(x: int, y: int):
    _arduino_send('CL')


def arduino_mouse_click_right(x: int, y: int):
    _arduino_send('CR')


def arduino_mouse_shift_click_left(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')


def arduino_mouse_shift_click_right(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    time.sleep(0.05)
    _arduino_send('CR')
    time.sleep(0.05)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')


def arduino_backspace(n: int):
    _arduino_send(f'BS,{n}')


_SHIFT_CHAR_MAP = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '<': ',', '>': '.', '?': '/',
    '~': '`',
}
VK_OEM_7 = 0xDE  # US keyboard apostrophe/quote key (' / ")

def _arduino_send_jamo(jamo: str):
    """자모 하나를 Arduino로 입력한다. 복합 자모는 분해해서 처리."""
    if jamo in hangul.COMPOUND_JAMO:
        for j in hangul.COMPOUND_JAMO[jamo]:
            _arduino_send_jamo(j)
        return
    key, shift = hangul.JAMO_KEY_MAP[jamo]
    vk = ord(key)
    if shift:
        _arduino_send(f'KD,{win32con.VK_SHIFT}')
    _arduino_send(f'KP,{vk}')
    if shift:
        _arduino_send(f'KU,{win32con.VK_SHIFT}')


def _arduino_send_hangul(ch: str):
    """한글 한 글자를 Arduino로 입력한다."""
    cho, jung, jong = hangul.decompose_hangul(ch)
    _arduino_send_jamo(cho)
    _arduino_send_jamo(jung)
    if jong:
        _arduino_send_jamo(jong)
    _arduino_send(f'KP,{win32con.VK_RIGHT}')  # IME 조합 버퍼 확정


def arduino_type_string(text: str):
    """문자열을 Arduino HID를 통해 한 글자씩 입력한다. 한글/영문/숫자/특수문자 지원."""
    VK_HANGUL = 0x15
    korean_mode = True  # 현재 입력 모드 (False=영어, True=한글)

    def set_mode(need_korean: bool):
        nonlocal korean_mode
        if korean_mode != need_korean:
            _arduino_send(f'KP,{VK_HANGUL}')
            korean_mode = need_korean

    for ch in text:
        is_korean = '\uAC00' <= ch <= '\uD7A3'

        if ch == ' ':
            _arduino_send(f'KP,{win32con.VK_SPACE}')
        elif is_korean:
            set_mode(True)
            _arduino_send_hangul(ch)
        elif ch.isalpha():
            set_mode(False)
            vk = ord(ch.upper())
            if ch.isupper():
                _arduino_send(f'KD,{win32con.VK_SHIFT}')
                _arduino_send(f'KP,{vk}')
                _arduino_send(f'KU,{win32con.VK_SHIFT}')
            else:
                _arduino_send(f'KP,{vk}')
        elif ch.isdigit():
            set_mode(False)
            _arduino_send(f'KP,{ord(ch)}')
        elif ch == "'":
            set_mode(False)
            _arduino_send(f'KP,{VK_OEM_7}')
        elif ch == '"':
            set_mode(False)
            _arduino_send(f'KD,{win32con.VK_SHIFT}')
            _arduino_send(f'KP,{VK_OEM_7}')
            _arduino_send(f'KU,{win32con.VK_SHIFT}')
        elif ch in _SHIFT_CHAR_MAP:
            set_mode(False)
            vk = ord(_SHIFT_CHAR_MAP[ch])
            _arduino_send(f'KD,{win32con.VK_SHIFT}')
            _arduino_send(f'KP,{vk}')
            _arduino_send(f'KU,{win32con.VK_SHIFT}')
        else:
            set_mode(False)
            _arduino_send(f'KP,{ord(ch)}')

    if not korean_mode:
        _arduino_send(f'KP,{VK_HANGUL}')  # 입력 후 한글 모드로 복원
    _arduino_send(f'KP,{win32con.VK_RETURN}')
    _arduino_send(f'KU,{win32con.VK_RETURN}')  # 엔터키는 두 번 입력해서 채팅창 확정


# ── Turn (방향 이동) ───────────────────────────────────────────────────────────
_TURN_XY = {
    'north':     (648, 228),
    'northeast': (754, 272),
    'east':      (839, 405),
    'southeast': (680, 410),
    'south':     (648, 528),
    'southwest': (542, 484),
    'west':      (436, 407),
    'northwest': (542, 272),
}
_DIRECTIONS = tuple(_TURN_XY.keys())

def turn_north():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['north'])
    current_direction = 'north'

def turn_northeast():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['northeast'])
    current_direction = 'northeast'

def turn_east():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['east'])
    current_direction = 'east'

def turn_southeast():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['southeast'])
    current_direction = 'southeast'

def turn_south():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['south'])
    current_direction = 'south'

def turn_southwest():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['southwest'])
    current_direction = 'southwest'

def turn_west():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['west'])
    current_direction = 'west'

def turn_northwest():
    global current_direction
    arduino_mouse_shift_click_left(*_TURN_XY['northwest'])
    current_direction = 'northwest'


def arduino_init_cursor():
    """커서를 화면 (0, 0) 으로 초기화한다. 프로그램 시작 시 한 번 호출 권장."""
    _arduino_send('INIT')

_mouse_key: str | None = None
current_direction = 'north'
available_count_1 = 0
mp_1 = 0
direction_threshold = 4
adena_per_pickup = 150
adena_three_pickup_price = 450
adena_six_pickup_price = 900
low_count_direction = 'southeast'
high_count_direction = 'northwest'
exchange_yes_button = (869, 914)  # 교환 수락 Yes 좌표
exchange_no_button = (917, 912)   # 교환 수락 No 좌표
_exchange_nickname_xy: tuple[int, int] | None = None
_RESTART_BUTTON_TEXT_REGION = (1058, 118, 112, 34)
_RESTART_MENU_CONFIRM_REGION = (1080, 174, 72, 28)
_RESTART_BUTTON_CLICK_XY = (1115, 135)
_RESTART_CLICK_SCREEN_Y_OFFSET = 25
_RESTART_TEXT_BRIGHT_THRESHOLD = 250
_RESTART_CONFIRM_BRIGHT_THRESHOLD = 120
_RESTART_CLICK_MIN_INTERVAL_SECONDS = 1.0
_RESTART_WATCH_INTERVAL_SECONDS = 0.5
_restart_click_lock = _threading.Lock()
_restart_watch_lock = _threading.Lock()
_restart_watch_thread: _threading.Thread | None = None
_restart_watch_stop: _threading.Event | None = None
_last_restart_click_time = 0.0
_F10_SLOT_CONTENT_REGION = (1070, 840, 54, 46)
_F10_SLOT_BRIGHT_THRESHOLD = 70.0
_F10_SLOT_SATURATION_THRESHOLD = 35
_F10_SLOT_PIXEL_COUNT_THRESHOLD = 50
_F10_SLOT_STD_THRESHOLD = 25.0
_OPPONENT_TRADE_SLOT_ORIGIN = (45, 498)
_OPPONENT_TRADE_SLOT_SIZE = 62
_OPPONENT_TRADE_SLOT_COLS = 4
_OPPONENT_TRADE_SLOT_ROWS = 3
_OPPONENT_TRADE_SLOT_MARGIN = 8
_OPPONENT_TRADE_SATURATED_PIXEL_THRESHOLD = 25
_OPPONENT_TRADE_STD_THRESHOLD = 18.0
_OPPONENT_TRADE_ADENA_YELLOW_THRESHOLD = 80
TARGET_CHECK_FAILED_MESSAGE_DEFAULT = "{nickname}님 타겟 확인이 안 됩니다. 다시 거래 부탁드립니다."


class RestartButtonClicked(RuntimeError):
    pass


def _load_macro_data() -> dict:
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)


def _load_target_check_failures() -> dict:
    try:
        with open(_TARGET_CHECK_FAILURES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"nicknames": {}}

    if not isinstance(data, dict):
        return {"nicknames": {}}

    nicknames = data.get("nicknames")
    if not isinstance(nicknames, dict):
        data["nicknames"] = {}
    return data


def record_target_check_failure(nickname: str | None) -> int:
    nickname = str(nickname).strip() if nickname else ""
    if not nickname:
        return 0

    data = _load_target_check_failures()
    nicknames = data.setdefault("nicknames", {})
    record = nicknames.get(nickname)

    if isinstance(record, dict):
        try:
            count_value = int(record.get("count", 0))
        except (TypeError, ValueError):
            count_value = 0
    else:
        try:
            count_value = int(record or 0)
        except (TypeError, ValueError):
            count_value = 0

    count_value += 1
    nicknames[nickname] = {
        "count": count_value,
        "last_failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    tmp_path = f"{_TARGET_CHECK_FAILURES_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, _TARGET_CHECK_FAILURES_PATH)

    return count_value


def get_target_check_failed_message(nickname: str | None, fail_count: int | None = None) -> str:
    data = _load_macro_data()
    message = str(data.get("target_check_failed_message", TARGET_CHECK_FAILED_MESSAGE_DEFAULT)).strip()
    if not message:
        message = TARGET_CHECK_FAILED_MESSAGE_DEFAULT

    try:
        return message.format(nickname=nickname or "", fail_count=fail_count or 0)
    except (IndexError, KeyError, ValueError):
        return message


def _load_price_config(data: dict) -> None:
    global adena_per_pickup, adena_three_pickup_price, adena_six_pickup_price
    adena_per_pickup = int(data["adena_per_pickup"])
    adena_three_pickup_price = int(data.get("adena_three_pickup_price", adena_per_pickup * 3))
    adena_six_pickup_price = int(data.get("adena_six_pickup_price", adena_per_pickup * 6))


def get_adena_three_pickup_price() -> int:
    return int(adena_three_pickup_price or adena_per_pickup * 3)


def get_adena_six_pickup_price() -> int:
    return int(adena_six_pickup_price or adena_per_pickup * 6)


def get_adena_price_notice() -> str:
    return (
        f"1방 {adena_per_pickup}원 "
        f"6방 {get_adena_six_pickup_price()}원!"
    )


def get_pickup_count_for_adena(received: int | float) -> int:
    try:
        received_int = int(received)
    except (TypeError, ValueError):
        return 0

    if received_int <= 0:
        return 0

    unit_price = int(adena_per_pickup)
    if unit_price <= 0:
        return 0

    three_price = get_adena_three_pickup_price()
    six_price = get_adena_six_pickup_price()

    if six_price > 0 and received_int == six_price:
        return 6
    if three_price > 0 and received_int == three_price:
        return 3
    return received_int // unit_price


def _coerce_xy(value, key: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise RuntimeError(f"invalid {key}: {value!r}")
    return int(value[0]), int(value[1])


def _resolve_direction_xy(data: dict, key: str, direction: str) -> tuple[int, int] | None:
    for config_key in (f"{key}_by_direction", key):
        value = data.get(config_key)
        if isinstance(value, dict):
            if direction in value:
                return _coerce_xy(value[direction], f"{config_key}.{direction}")
            if "default" in value:
                return _coerce_xy(value["default"], f"{config_key}.default")
        elif value is not None:
            return _coerce_xy(value, config_key)
    return None


def get_configured_mouse_xy(
    key: str | None = None,
    fallback_key: str | None = None,
    direction: str | None = None,
) -> tuple[int, int]:
    if key is None:
        key = _mouse_key
    if key is None:
        raise RuntimeError("mouse key is not initialized. call init_setting() first.")

    data = _load_macro_data()
    direction = direction or current_direction
    if not isinstance(direction, str):
        direction = str(direction)
    keys = [key]
    if fallback_key and fallback_key not in keys:
        keys.append(fallback_key)

    for config_key in keys:
        xy = _resolve_direction_xy(data, config_key, direction)
        if xy is not None:
            return xy

    raise RuntimeError(f"missing mouse coordinate config for {keys!r}")


def set_hwnd(hwnd: int):
    global lineage1_hwnd
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"유효하지 않은 HWND: {hwnd}")
    lineage1_hwnd = hwnd
    print(f"[macro] HWND 설정됨: {hwnd} ({win32gui.GetWindowText(hwnd)})")


def _find_lineage_hwnd() -> int:
    result = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).startswith("Lineage Classic"):
            result.append(hwnd)
    win32gui.EnumWindows(callback, None)
    if not result:
        raise RuntimeError("'Lineage Classic'으로 시작하는 윈도우를 찾을 수 없습니다.")
    return result[0]


def get_hwnd() -> int:
    global lineage1_hwnd
    if lineage1_hwnd is None:
        lineage1_hwnd = _find_lineage_hwnd()
        print(f"[macro] HWND 자동 설정됨: {lineage1_hwnd} ({win32gui.GetWindowText(lineage1_hwnd)})")
    return lineage1_hwnd


def init_setting(role: str):
    """
    role: "server" 또는 "client"
    1. "Lineage Classic"으로 시작하는 윈도우를 찾아 타이틀 설정 및 lineage1_hwnd 지정
    2. macro_data.json에서 설정 로드:
       - direction 설정은 공통 적용
       - mouse x,y는 타이틀에 따라 server/client/client_numbering 키 사용
    """
    global lineage1_hwnd
    global _mouse_key
    global direction_threshold, current_direction, low_count_direction, high_count_direction
    global _TURN_XY

    # ── 윈도우 탐색 및 타이틀 설정 ────────────────────────────────────────────
    all_windows: dict[str, int] = {}
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            all_windows[win32gui.GetWindowText(hwnd)] = hwnd
    win32gui.EnumWindows(callback, None)

    if role == "server":
        if "server" in all_windows:
            lineage1_hwnd = all_windows["server"]
            new_title = "server"
        else:
            candidates = [hwnd for title, hwnd in all_windows.items() if title.startswith("Lineage Classic")]
            if not candidates:
                raise RuntimeError("'Lineage Classic'으로 시작하는 윈도우를 찾을 수 없습니다.")
            lineage1_hwnd = candidates[0]
            win32gui.SetWindowText(lineage1_hwnd, "server")
            new_title = "server"
    else:
        candidates = [hwnd for title, hwnd in all_windows.items() if title.startswith("Lineage Classic")]
        if "server" in all_windows:
            if "client" in all_windows:
                lineage1_hwnd = all_windows["client"]
                new_title = "client"
            else:
                if not candidates:
                    raise RuntimeError("'Lineage Classic'으로 시작하는 윈도우를 찾을 수 없습니다.")
                lineage1_hwnd = candidates[0]
                win32gui.SetWindowText(lineage1_hwnd, "client")
                new_title = "client"
        else:
            if not candidates:
                raise RuntimeError("'Lineage Classic'으로 시작하는 윈도우를 찾을 수 없습니다.")
            if "client" not in all_windows:
                new_title = "client"
            else:
                n = 2
                while f"client{n}" in all_windows:
                    n += 1
                new_title = f"client{n}"
            lineage1_hwnd = candidates[0]
            win32gui.SetWindowText(lineage1_hwnd, new_title)

    rect = win32gui.GetWindowRect(lineage1_hwnd)
    win32gui.MoveWindow(lineage1_hwnd, 0, 0, rect[2] - rect[0], rect[3] - rect[1], True)
    print(f"[macro] lineage1_hwnd={lineage1_hwnd} → 타이틀 '{new_title}', 위치 (0, 0)")

    # ── JSON 설정 로드 ─────────────────────────────────────────────────────────
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    # 타이틀에 따라 mouse x,y 키 결정
    if new_title == "server":
        mouse_key = "server_mouse_x_y"
    elif new_title == "client":
        mouse_key = "client_mouse_x_y"
    else:  # client2, client3, ...
        mouse_key = "client_numbering_mouse_x_y"

    _mouse_key = mouse_key

    direction_threshold = data["direction_threshold"]
    _load_price_config(data)
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    for d in _DIRECTIONS:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])

    print(f"[macro] mouse_key={mouse_key}")
    print(f"[macro] direction_threshold={direction_threshold}, current={current_direction}, low={low_count_direction}, high={high_count_direction}")
    print(
        f"[macro] adena_per_pickup={adena_per_pickup}, "
        f"adena_three_pickup_price={get_adena_three_pickup_price()}, "
        f"adena_six_pickup_price={get_adena_six_pickup_price()}"
    )
    print(f"[macro] turn_xy={_TURN_XY}")


def init_custom_hwnd(title: str, role: str = "client"):
    """
    title: 찾을 윈도우 타이틀 이름 (해당 타이틀의 윈도우를 lineage1_hwnd로 지정)
    role: mouse x,y 키 결정에 사용 ("server" / "client" / 그 외 → client_numbering)
    1. 해당 타이틀의 윈도우를 찾아 lineage1_hwnd로 지정
    2. 없으면 RuntimeError 발생
    3. macro_data.json에서 설정 로드:
       - direction 설정은 공통 적용
       - mouse x,y는 role에 따라 server/client/client_numbering 키 사용
    """
    global lineage1_hwnd
    global _mouse_key
    global direction_threshold, current_direction, low_count_direction, high_count_direction
    global _TURN_XY

    # ── 타이틀로 윈도우 탐색 ──────────────────────────────────────────────────
    all_windows: dict[str, int] = {}
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            all_windows[win32gui.GetWindowText(hwnd)] = hwnd
    win32gui.EnumWindows(callback, None)

    candidates = [hwnd for t, hwnd in all_windows.items() if t.startswith(title)]
    if not candidates:
        raise RuntimeError(f"'{title}'으로 시작하는 윈도우를 찾을 수 없습니다.")
    lineage1_hwnd = candidates[0]

    rect = win32gui.GetWindowRect(lineage1_hwnd)
    win32gui.MoveWindow(lineage1_hwnd, 0, 0, rect[2] - rect[0], rect[3] - rect[1], True)
    print(f"[macro] lineage1_hwnd={lineage1_hwnd} → 타이틀 '{win32gui.GetWindowText(lineage1_hwnd)}', 위치 (0, 0)")

    # ── JSON 설정 로드 ─────────────────────────────────────────────────────────
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    # role에 따라 mouse x,y 키 결정
    if role == "server":
        mouse_key = "server_mouse_x_y"
    elif role == "client":
        mouse_key = "client_mouse_x_y"
    else:
        mouse_key = "client_numbering_mouse_x_y"

    _mouse_key = mouse_key

    direction_threshold = data["direction_threshold"]
    _load_price_config(data)
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    for d in _DIRECTIONS:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])

    print(f"[macro] mouse_key={mouse_key}")
    print(f"[macro] direction_threshold={direction_threshold}, current={current_direction}, low={low_count_direction}, high={high_count_direction}")
    print(
        f"[macro] adena_per_pickup={adena_per_pickup}, "
        f"adena_three_pickup_price={get_adena_three_pickup_price()}, "
        f"adena_six_pickup_price={get_adena_six_pickup_price()}"
    )
    print(f"[macro] turn_xy={_TURN_XY}")


def key_down(vk: int):
    arduino_key_down(vk)


def key_up(vk: int):
    arduino_key_up(vk)


def key_press(vk: int, duration: float = 0.05):
    arduino_key_press(vk, duration)


def move_window(x: int, y: int):
    hwnd = get_hwnd()
    rect = win32gui.GetWindowRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    win32gui.MoveWindow(hwnd, x, y, width, height, True)


def screenshot(filename: str = None, hwnd: int = None) -> Image.Image:
    with _screenshot_lock:
        if hwnd is None:
            hwnd = get_hwnd()
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"invalid window handle for screenshot: {hwnd}")
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)

        rect = win32gui.GetWindowRect(hwnd)
        w = int((rect[2] - rect[0]))
        h = int((rect[3] - rect[1]))
        if w <= 0 or h <= 0:
            raise RuntimeError(f"invalid window size for screenshot: hwnd={hwnd}, size={w}x{h}")

        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        bitmap = None
        try:
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            if not hwnd_dc:
                raise RuntimeError(f"GetWindowDC failed: hwnd={hwnd}")
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)

            bmpinfo = bitmap.GetInfo()
            bmpstr = bitmap.GetBitmapBits(True)
            img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)
        finally:
            if bitmap is not None:
                try:
                    handle = bitmap.GetHandle()
                    if handle:
                        win32gui.DeleteObject(handle)
                except Exception:
                    pass
            if save_dc is not None:
                try:
                    save_dc.DeleteDC()
                except Exception:
                    pass
            if mfc_dc is not None:
                try:
                    mfc_dc.DeleteDC()
                except Exception:
                    pass
            if hwnd_dc is not None:
                try:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
                except Exception:
                    pass

    img = img.crop((0, 0, img.width - 16, img.height - 41))

    if filename is None:
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"

    os.makedirs("image", exist_ok=True)
    # path = os.path.join("image", filename)
    # img.save(path)
    # print(f"[macro] 스크린샷 저장됨: {path}")
    return img


def mouse_click_left(x: int, y: int):
    arduino_mouse_click_left(x, y)
    time.sleep(0.3)


def mouse_click_right(x: int, y: int):
    arduino_mouse_click_right(x, y)


def _send_char(ch: str):
    hangul.send_char(ch, get_hwnd())


def _backspace(n: int):
    arduino_backspace(n)


def force_set_foreground_window(hwnd: int):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    windll.user32.keybd_event(0, 0, 0, 0)  # null 입력으로 포그라운드 권한 획득
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.05)

def arduino_mouse_move_rel(dx: int, dy: int):
    return _arduino_send(f"RM,{dx},{dy}")

def shake_mouse_small(count=10, dist=10, delay=0.05):
    for _ in range(count):
        arduino_mouse_move_rel(dist, 0) # 오른쪽으로 2
        time.sleep(delay)
        arduino_mouse_move_rel(-dist, 0) # 왼쪽으로 2
        time.sleep(delay)

def use_potion():
    force_set_foreground_window(lineage1_hwnd)
    time.sleep(0.5)
    _arduino_send(f'KP,{win32con.VK_F8}')


def pickup_lineage1(
    target_nickname: str | None = None,
    direction: str | None = None,
    log_messages: list[str] | None = None,
    print_logs: bool = True,
    log_prefix: str = "[macro]",
) -> bool:
    def log(message: str) -> None:
        line = f"{log_prefix} {message}" if log_prefix else message
        if log_messages is not None:
            log_messages.append(line)
        if print_logs:
            print(line)

    x, y = get_configured_mouse_xy(direction=direction)
    force_set_foreground_window(lineage1_hwnd)
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)

    expected_nickname = str(target_nickname).strip() if target_nickname else ""
    if expected_nickname:
        for attempt in range(10):
            arduino_mouse_shift_click_right(x, y)
            time.sleep(0.1)
            img = screenshot(hwnd=lineage1_hwnd)
            input_text = readInputText(img).strip()
            log(f"타겟 확인 ({attempt+1}/10) - read='{input_text}', expected='{expected_nickname}'")
            arduino_key_down(win32con.VK_CONTROL)
            arduino_key_press(win32con.VK_BACK)
            arduino_key_up(win32con.VK_CONTROL)
            time.sleep(0.1)
            if input_text == expected_nickname:
                log("타겟 고정 성공")
                break
        else:
            failure_count = record_target_check_failure(expected_nickname)
            log(f"타겟 고정 실패 - 현재 픽업 스킵, 누적 실패={failure_count}")
            arduino_type_string(get_target_check_failed_message(expected_nickname, failure_count))
            return False

    key_press(win32con.VK_F5)
    time.sleep(0.1)
    mouse_click_left(x, y)
    time.sleep(0.1)
    return True



def checkExchangeRequest(img=None) -> bool:
    if img is None:
        img = screenshot()
    r, g, b = img.getpixel((848, 877))
    print(f"[macro] 교환 요청 픽셀 RGB: ({r}, {g}, {b})")
    return (r, g, b) == (0, 0, 0)


def get_brightness(image: Image.Image) -> float:
    """이미지의 평균 밝기(0.0~255.0)를 반환한다."""
    arr = np.array(image.convert('RGB'), dtype=np.float32)
    return float(arr.mean())


def _opponent_trade_slot_boxes() -> list[tuple[int, int, int, int]]:
    origin_x, origin_y = _OPPONENT_TRADE_SLOT_ORIGIN
    size = _OPPONENT_TRADE_SLOT_SIZE
    return [
        (origin_x + col * size, origin_y + row * size, size, size)
        for row in range(_OPPONENT_TRADE_SLOT_ROWS)
        for col in range(_OPPONENT_TRADE_SLOT_COLS)
    ]


def _analyze_trade_slot(slot: Image.Image) -> dict[str, float | int | bool]:
    margin = _OPPONENT_TRADE_SLOT_MARGIN
    width, height = slot.size
    center = slot.crop((margin, margin, width - margin, height - margin))
    arr = np.array(center.convert("RGB"), dtype=np.int16)
    brightness = arr.mean(axis=2)
    saturation = arr.max(axis=2) - arr.min(axis=2)
    yellow = (
        (arr[:, :, 0] > 90)
        & (arr[:, :, 1] > 80)
        & ((arr[:, :, 0] - arr[:, :, 2]) > 35)
        & ((arr[:, :, 1] - arr[:, :, 2]) > 25)
        & (arr[:, :, 0] >= arr[:, :, 1] - 25)
        & (arr[:, :, 0] <= arr[:, :, 1] + 80)
    )
    saturated_pixels = int((saturation > 45).sum())
    yellow_pixels = int(yellow.sum())
    std = float(arr.std())
    occupied = (
        saturated_pixels >= _OPPONENT_TRADE_SATURATED_PIXEL_THRESHOLD
        or std >= _OPPONENT_TRADE_STD_THRESHOLD
    )
    return {
        "occupied": occupied,
        "adena_like": yellow_pixels >= _OPPONENT_TRADE_ADENA_YELLOW_THRESHOLD,
        "saturated_pixels": saturated_pixels,
        "yellow_pixels": yellow_pixels,
        "std": std,
        "mean": float(brightness.mean()),
    }


def analyze_opponent_trade_items(img: Image.Image | None = None) -> dict[str, object]:
    if img is None:
        img = screenshot()

    slots = []
    occupied_slots = []
    for idx, (x, y, width, height) in enumerate(_opponent_trade_slot_boxes()):
        if x < 0 or y < 0 or x + width > img.width or y + height > img.height:
            continue
        features = _analyze_trade_slot(crop(img, x, y, width, height))
        slot_no = idx + 1
        features["slot"] = slot_no
        slots.append(features)
        if features["occupied"]:
            occupied_slots.append(slot_no)

    first_slot = slots[0] if slots else {}
    if not occupied_slots:
        state = "empty"
    elif occupied_slots == [1] and first_slot.get("adena_like"):
        state = "adena_only"
    else:
        state = "invalid"

    return {
        "state": state,
        "occupied_slots": occupied_slots,
        "slots": slots,
    }


def is_f10_slot_occupied(img: Image.Image | None = None) -> bool:
    if img is None:
        img = screenshot()

    x, y, width, height = _F10_SLOT_CONTENT_REGION
    if x < 0 or y < 0 or x + width > img.width or y + height > img.height:
        return False

    arr = np.array(crop(img.convert("RGB"), x, y, width, height))
    brightness = arr.mean(axis=2)
    saturation = arr.max(axis=2) - arr.min(axis=2)
    bright_pixels = int((brightness > _F10_SLOT_BRIGHT_THRESHOLD).sum())
    saturated_pixels = int((saturation > _F10_SLOT_SATURATION_THRESHOLD).sum())
    return (
        bright_pixels >= _F10_SLOT_PIXEL_COUNT_THRESHOLD
        or saturated_pixels >= _F10_SLOT_PIXEL_COUNT_THRESHOLD
        or float(arr.std()) >= _F10_SLOT_STD_THRESHOLD
    )


def clear_f10_slot_if_occupied(
    img: Image.Image | None = None,
    timeout_seconds: float = 30.0,
    check_interval: float = 0.2,
    print_log: bool = True,
) -> float:
    hwnd = get_hwnd()
    if img is None:
        img = screenshot(hwnd=hwnd)
    if not is_f10_slot_occupied(img):
        return 0.0

    force_set_foreground_window(hwnd)
    started_at = time.time()
    deadline = started_at + max(0.1, timeout_seconds)
    key_down(win32con.VK_F10)
    try:
        while time.time() < deadline:
            time.sleep(max(0.05, check_interval))
            img = screenshot(hwnd=hwnd)
            if not is_f10_slot_occupied(img):
                elapsed = time.time() - started_at
                if print_log:
                    print(f"[macro] F10 slot cleared - held_seconds={elapsed:.1f}")
                return elapsed
    finally:
        key_up(win32con.VK_F10)

    elapsed = time.time() - started_at
    if print_log:
        print(f"[macro] F10 slot still occupied after held_seconds={elapsed:.1f}")
    return elapsed


def _count_bright_pixels(image: Image.Image, region: tuple[int, int, int, int], threshold: int = 235) -> int:
    x, y, width, height = region
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        return 0

    arr = np.array(crop(image.convert("RGB"), x, y, width, height))
    mask = (arr[:, :, 0] >= threshold) & (arr[:, :, 1] >= threshold) & (arr[:, :, 2] >= threshold)
    return int(mask.sum())


def is_restart_button_visible(img: Image.Image | None = None) -> bool:
    if img is None:
        img = screenshot()

    restart_bright = _count_bright_pixels(img, _RESTART_BUTTON_TEXT_REGION)
    confirm_bright = _count_bright_pixels(img, _RESTART_MENU_CONFIRM_REGION)
    return (
        restart_bright >= _RESTART_TEXT_BRIGHT_THRESHOLD
        and confirm_bright >= _RESTART_CONFIRM_BRIGHT_THRESHOLD
    )


def click_restart_if_visible(img: Image.Image | None = None, print_log: bool = True) -> bool:
    global _last_restart_click_time

    if img is None:
        img = screenshot()
    if not is_restart_button_visible(img):
        return False

    now = time.time()
    with _restart_click_lock:
        if now - _last_restart_click_time < _RESTART_CLICK_MIN_INTERVAL_SECONDS:
            return False
        _last_restart_click_time = now

    hwnd = get_hwnd()
    force_set_foreground_window(hwnd)
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    x, y = _RESTART_BUTTON_CLICK_XY
    screen_x = left + x
    screen_y = top + y + _RESTART_CLICK_SCREEN_Y_OFFSET
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.05)
    arduino_mouse_move_rel(0, 20)
    time.sleep(0.03)
    arduino_mouse_move_rel(0, -20)
    time.sleep(0.05)
    arduino_mouse_click_left(screen_x, screen_y)
    if print_log:
        print(
            f"[macro] Restart button detected -> click ({x}, {y}) "
            f"screen_y_offset={_RESTART_CLICK_SCREEN_Y_OFFSET}"
        )
    time.sleep(0.5)
    return True


def start_restart_watcher(
    on_click=None,
    interval: float = _RESTART_WATCH_INTERVAL_SECONDS,
    print_log: bool = True,
) -> None:
    global _restart_watch_thread, _restart_watch_stop

    with _restart_watch_lock:
        if _restart_watch_thread is not None and _restart_watch_thread.is_alive():
            return

        stop_event = _threading.Event()
        _restart_watch_stop = stop_event

        def _watch() -> None:
            while not stop_event.is_set():
                try:
                    if click_restart_if_visible(print_log=print_log) and on_click is not None:
                        on_click()
                except Exception as exc:
                    if print_log:
                        print(f"[macro] Restart watcher error - error={exc}")
                stop_event.wait(max(0.1, interval))

        _restart_watch_thread = _threading.Thread(
            target=_watch,
            name="restart-watcher",
            daemon=True,
        )
        _restart_watch_thread.start()


def stop_restart_watcher() -> None:
    with _restart_watch_lock:
        if _restart_watch_stop is not None:
            _restart_watch_stop.set()


def readMp(img=None) -> int | None:
    if img is None:
        img = screenshot()
    if click_restart_if_visible(img):
        raise RestartButtonClicked("Restart button clicked")
    for dx in (0, 5, 10):
        cropped = crop(img, 976 + dx, 96, 100, 21)
        text = read_text(cropped, 0, 0, (0xCC, 0xE3, 0xFF))
        parts = text.split('/')
        digits = ''.join(c for c in parts[0] if c.isdigit())
        if digits:
            return int(digits)
    return None


def readAdena(max_attempts: int | None = None, key_delay: float = 0.2) -> int | None:
    force_set_foreground_window(lineage1_hwnd)
    attempts = 0
    while max_attempts is None or attempts < max_attempts:
        attempts += 1
        key_press(win32con.VK_F9)
        if key_delay > 0:
            time.sleep(key_delay)
        img = screenshot()
        cropped = crop(img, 228 + 60 + 5 + 5, 883, 500, 21)
        text = read_text(cropped, 0, 0, (0xFF, 0xF1, 0xB5))
        if '(' in text and ')' in text:
            inner = text[text.index('(') + 1:text.index(')')]
            digits = inner.replace(' ', '')
            try:
                value = int(digits)
            except (ValueError, TypeError):
                continue
            if value == 0:
                continue
            return value
        time.sleep(0.5)
    return None


def readExchangeNickname(img=None):
    global _exchange_nickname_xy
    if img is None:
        img = screenshot()
    if _exchange_nickname_xy is None:
        _exchange_nickname_xy = findExchangeNicknameY(img)
        if _exchange_nickname_xy is None:
            return ''
        print(f"[macro] exchange nickname xy 세팅됨: {_exchange_nickname_xy}")
    _, y = _exchange_nickname_xy
    return _read_exchange_nickname_img(img, y)


def acceptExchange():
    win32api.SetCursorPos((247, 752))
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    arduino_key_press(ord('Y'))
    time.sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')
    time.sleep(0.3)


def cancelExchange():
    win32api.SetCursorPos((312, 752))
    time.sleep(0.2)
    _arduino_send('CL')
    time.sleep(0.3)


def findExchangeNicknameY(img=None) -> tuple[int, int] | None:
    """y=480에서 50까지 스캔하며 닉네임 텍스트가 처음 발견되는 (x, y) 좌표를 반환한다."""
    if img is None:
        img = screenshot()
    w, h = 140, 24
    color = (255, 255, 255)
    for y in range(480, 49, -1):
        for x in range(107, 56, -5):
            cropped = crop(img, x, y, w, h)
            text = read_text(cropped, 0, 0, color)
            if text:
                return (x, y)
    return None


def readInputText(img=None) -> str:
    if img is None:
        img = screenshot()
    return read_text(img, 249, 933, (0xff, 0xff, 0xff)).replace('|', '')


def monitor_chat():
    prev = None
    while True:
        img = screenshot()
        cropped = crop(img, 228, 907, 140, 25)
        text = read_text(cropped, 0, 0, (0xAF, 0xEB, 0xEB))
        if text != prev:
            print(text)
            prev = text
        time.sleep(0.5)


_DIRECTION_FUNCS = {
    'north': turn_north, 'northeast': turn_northeast,
    'east': turn_east, 'southeast': turn_southeast,
    'south': turn_south, 'southwest': turn_southwest,
    'west': turn_west, 'northwest': turn_northwest,
}


def turn_to(direction: str, force: bool = False, settle_delay: float = 1.0) -> bool:
    func = _DIRECTION_FUNCS.get(direction)
    if func is None:
        print(f"[macro] 알 수 없는 방향: {direction!r}")
        return False
    if not force and current_direction == direction:
        return False

    force_set_foreground_window(lineage1_hwnd)
    func()
    if settle_delay > 0:
        time.sleep(settle_delay)
    return True


def turn_random_excluding(excluded_direction: str, settle_delay: float = 1.0) -> str | None:
    data = _load_macro_data()
    blocked = data.get("blocked_turn_directions", [])
    if isinstance(blocked, str):
        blocked_directions = {blocked}
    elif isinstance(blocked, (list, tuple, set)):
        blocked_directions = {str(direction) for direction in blocked}
    else:
        blocked_directions = set()

    excluded = {excluded_direction, current_direction, *blocked_directions}
    candidates = [direction for direction in _DIRECTION_FUNCS if direction not in excluded]
    if not candidates:
        print(f"[macro] random turn candidates empty. excluded={excluded_direction!r}, blocked={sorted(blocked_directions)}")
        return None

    direction = random.choice(candidates)
    if turn_to(direction, settle_delay=settle_delay):
        return direction
    return None


def sync_direction(force: bool = True, settle_delay: float = 1.0) -> bool:
    return turn_to(current_direction, force=force, settle_delay=settle_delay)

