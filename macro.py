from itertools import count
import os
import sys
import json
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


def _proxy_connect():
    global _proxy_conn
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.connect((_PROXY_HOST, _PROXY_PORT))
    _proxy_conn = s
    print(f"[macro] Arduino proxy 연결됨: {_PROXY_HOST}:{_PROXY_PORT}")


# ── Arduino HID 래퍼 ──────────────────────────────────────────────────────────
# 기존 winapi 함수(key_down / key_up 등)와 동일한 인터페이스.
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

# ── 우상단 상태창 OCR ─────────────────────────────────────────────────────────
# 상태창은 가방 등에 가려지지 않는 한 항상 정확하다(배경이 일정). 이 값을 정답으로
# 삼아 하단 바 OCR(read_hp/read_mp)을 보정/검증한다.
#   HP: y=71, 글자색 (247,201,227) 흰빛 분홍
#   MP: y=96, 글자색 (204,227,255) 흰빛 하늘
def _read_status_value(img, y: int, color: tuple) -> int:
    for dx in (0, 5, 10):
        cropped = crop(img, 976 + dx, y, 120, 21)
        text = read_text(cropped, 0, 0, color)
        parts = text.split('/')
        digits = ''.join(c for c in parts[0] if c.isdigit())
        if digits:
            return int(digits)
    return 0


def read_mp_with_status(img=None) -> int:
    """우상단 상태창에서 현재 MP를 읽는다(정확값). 실패 시 0."""
    if img is None:
        img = screenshot()
    return _read_status_value(img, 96, (0xCC, 0xE3, 0xFF))


def read_hp_with_status(img=None) -> int:
    """우상단 상태창에서 현재 HP를 읽는다(정확값). 실패 시 0.
    read_mp_with_status와 동일한 로직, 위치(y=71)와 글자색만 다르다."""
    if img is None:
        img = screenshot()
    return _read_status_value(img, 71, (247, 201, 227))

def arduino_mouse_move(x: int, y: int):
    _arduino_send(f'MM,{x},{y}')


def arduino_mouse_click_left():
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
    ':': ';', '"': "'", '<': ',', '>': '.', '?': '/',
    '~': '`',
}

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
    'southeast': (754, 484),
    'south':     (648, 528),
    'southwest': (542, 484),
    'west':      (436, 407),
    'northwest': (542, 272),
}

# 북(north) 기준 각 방향의 픽업 좌표 오프셋 (dx, dy)
_DIR_OFFSETS: dict[str, tuple[int, int]] = {
    'north':     (0,    0),
    'northeast': (40,   20),
    'east':      (80,   40),
    'southeast': (40,   60),
    'south':     (0,    80),
    'southwest': (-40,  60),
    'west':      (-80,  40),
    'northwest': (-40,  20),
}

# 모든 방향 쌍 간의 델타 (from_dir → to_dir)
DIRECTION_DELTAS: dict[tuple[str, str], tuple[int, int]] = {
    (frm, to): (
        _DIR_OFFSETS[to][0] - _DIR_OFFSETS[frm][0],
        _DIR_OFFSETS[to][1] - _DIR_OFFSETS[frm][1],
    )
    for frm in _DIR_OFFSETS
    for to in _DIR_OFFSETS
}

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


def apply_coord_delta(dx: int, dy: int):
    """서버 방향 전환 시 픽업 좌표를 이동시킨다."""
    _mouse_xy[0] += dx
    _mouse_xy[1] += dy
    print(f"[macro] 좌표 이동 적용: dx={dx:+}, dy={dy:+} → {_mouse_xy}")


def reset_coord():
    """픽업 좌표를 config 초기값으로 되돌린다."""
    with open("macro_data.json", encoding="utf-8") as f:
        data = json.load(f)
    _mouse_xy[:] = data[_mouse_key]
    print(f"[macro] 좌표 초기화: {_mouse_xy}")


def add_to_blocked_list(nickname: str):
    """blocked_list에 닉네임을 추가하고 macro_data.json에 저장한다."""
    if nickname in blocked_list:
        return
    blocked_list.append(nickname)
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    data["blocked_list"] = blocked_list
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"[macro] blocked_list 추가: {nickname} → {blocked_list}")

_mouse_key: str | None = None
_mouse_xy: list[int] = [0, 0]  # 런타임 픽업 좌표 (방향 전환 시 갱신)
current_direction = 'north'
available_count_1 = 0
mp_1 = 0
current_hp = 100
max_hp = 100
direction_threshold = 4
adena_per_pickup = 150
low_count_direction = 'southeast'
high_count_direction = 'northwest'
blocked_list: list[str] = []
exchange_yes_button = (869, 914)  # 교환 수락 Yes 좌표
exchange_no_button = (917, 912)   # 교환 수락 No 좌표
_exchange_nickname_xy: tuple[int, int] | None = None


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
    global direction_threshold, adena_per_pickup, current_direction, low_count_direction, high_count_direction
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
    _mouse_xy[:] = data[mouse_key]

    direction_threshold = data["direction_threshold"]
    adena_per_pickup = data["adena_per_pickup"]
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    blocked_list[:] = data.get("blocked_list", [])
    for d in ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])

    print(f"[macro] mouse_key={mouse_key}, mouse_xy={_mouse_xy}")
    print(f"[macro] direction_threshold={direction_threshold}, current={current_direction}, low={low_count_direction}, high={high_count_direction}")
    print(f"[macro] blocked_list={blocked_list}")
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
    global direction_threshold, adena_per_pickup, current_direction, low_count_direction, high_count_direction
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
    _mouse_xy[:] = data[mouse_key]

    direction_threshold = data["direction_threshold"]
    adena_per_pickup = data["adena_per_pickup"]
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    blocked_list[:] = data.get("blocked_list", [])
    for d in ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])

    print(f"[macro] mouse_key={mouse_key}, mouse_xy={_mouse_xy}")
    print(f"[macro] direction_threshold={direction_threshold}, current={current_direction}, low={low_count_direction}, high={high_count_direction}")
    print(f"[macro] blocked_list={blocked_list}")
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
    if hwnd is None:
        hwnd = get_hwnd()
    rect = win32gui.GetWindowRect(hwnd)
    w = int((rect[2] - rect[0]))
    h = int((rect[3] - rect[1]))

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bitmap)

    windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)

    bmpinfo = bitmap.GetInfo()
    bmpstr = bitmap.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)

    win32gui.DeleteObject(bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    img = img.crop((0, 0, img.width - 16, img.height - 41))

    if filename is None:
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"

    os.makedirs("image", exist_ok=True)
    # path = os.path.join("image", filename)
    # img.save(path)
    # print(f"[macro] 스크린샷 저장됨: {path}")
    return img



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


def heal_and_logout():
    force_set_foreground_window(lineage1_hwnd)
    time.sleep(0.1)
    arduino_key_press(win32con.VK_F6)
    arduino_key_down(win32con.VK_CONTROL)
    arduino_key_down(ord('Q'))
    arduino_key_up(ord('Q'))
    arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)
    win32api.SetCursorPos((1123,167))
    time.sleep(0.1)
    arduino_mouse_click_left()
    time.sleep(0.1)


def relogin(timeout: float = 40.0):
    """heal_and_logout()로 로그아웃한 캐릭터를 재접속한다.

    (287,322) → (1126,850) 순서로 클릭하여 재접속한 뒤, HP/MP 바가 다시
    읽힐 때까지 대기하고 F6을 한 번 눌러 마무리한다.
    """
    force_set_foreground_window(lineage1_hwnd)
    time.sleep(0.5)
    win32api.SetCursorPos((287, 322))
    time.sleep(0.3)
    arduino_mouse_click_left()
    time.sleep(0.3)
    win32api.SetCursorPos((1126, 850))
    time.sleep(0.3)
    arduino_mouse_click_left()

    # 접속 후 HP/MP 바가 다시 보일 때까지 대기
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = screenshot(hwnd=lineage1_hwnd)
        hp_cur, _ = read_bar_stat(img, "HP")
        mp_cur, _ = read_bar_stat(img, "MP")
        if hp_cur is not None and mp_cur is not None:
            print(f"[macro] 재접속 완료 - HP:{hp_cur}, MP:{mp_cur}")
            break
        time.sleep(1)
    else:
        print("[macro] 재접속 대기 시간 초과 - F6 진행")

    arduino_key_press(win32con.VK_F6)


def pickup_lineage1(target_nickname: str | None = None):
    x, y = _mouse_xy
    force_set_foreground_window(lineage1_hwnd)
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)

    for attempt in range(4):
        arduino_mouse_shift_click_right(x, y)
        time.sleep(0.1)
        img = screenshot(hwnd=lineage1_hwnd)
        input_text = readInputText(img)
        print(f"[macro] 타겟 확인 ({attempt+1}/4): '{input_text}' == '{target_nickname}'?")
        arduino_key_down(win32con.VK_CONTROL)
        arduino_key_press(win32con.VK_BACK)
        arduino_key_up(win32con.VK_CONTROL)
        time.sleep(0.1)
        if input_text == target_nickname:
            print("[macro] 타겟 고정 성공")
            break
    else:
        print(f"[macro] 타겟 고정 실패 - {x} , {y}")

    key_press(win32con.VK_F5)
    time.sleep(0.1)
    arduino_mouse_click_left()
    time.sleep(0.1)



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


# ── 하단 피통바 OCR (HP=빨간 바, MP=파란 바) ──────────────────────────────────
# 상태창(우상단)이 가방 등으로 가려져도 항상 보이는 하단 바에서 HP/MP를 읽는다.
# 각 숫자는 폭 10px 고정 셀에 그려진다. 정렬 때문에 한쪽 끝 셀이 자릿수와 무관하게
# 고정이다:  HP=우측정렬(최대값 끝자리 셀 x=527, 오른쪽→왼쪽),
#            MP=좌측정렬(현재값 첫자리 셀 x=717, 왼쪽→오른쪽).
#
# [코어색 박스로 글자 분리]  바 게이지가 줄면 글자 뒤 배경이 빨강(가득)→어두움(빈칸)
# 으로 바뀐다. 하지만 글자의 불투명 코어색은 배경·잔량과 무관하게 일정하다
# (HP=흰빛분홍 (255,213,213), MP=흰빛하늘 (200,206,255)). 그 코어색 ±tol 박스만 잡으면
# 가장자리 변동이 빠져 숫자 하나가 (거의) 좌표문자열 1개로 모인다. 셀의 좌표문자열을
# {좌표문자열: 숫자} 사전에서 dict.get() 한 번에 조회한다.
# 사전은 build_bar_templates.py 가 라벨된 스크린샷들로 생성한 bar_templates.json.
# (초록=중독 프레임은 학습에서 제외했다.)

_BAR_CONFIGS: dict[str, dict] = {
    "HP": {"y0": 667, "h": 15, "center": (255, 213, 213), "tol": 5, "anchor": 527, "step": -10, "bound": 430},
    "MP": {"y0": 667, "h": 14, "center": (200, 206, 255), "tol": 5, "anchor": 717, "step": 10, "bound": 840},
}

# stat -> {좌표문자열: 숫자}
_BAR_TEMPLATES: dict[str, dict[str, str]] = {}


def _load_bar_templates() -> None:
    path = os.path.join(_BASE, "bar_templates.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for stat, cfg in data.items():
        if stat not in _BAR_CONFIGS:
            continue
        if "center" in cfg:
            _BAR_CONFIGS[stat]["center"] = tuple(cfg["center"])
        if "tol" in cfg:
            _BAR_CONFIGS[stat]["tol"] = cfg["tol"]
        if "y0" in cfg:
            _BAR_CONFIGS[stat]["y0"] = cfg["y0"]
        if "h" in cfg:
            _BAR_CONFIGS[stat]["h"] = cfg["h"]
        _BAR_TEMPLATES[stat] = dict(cfg.get("digits", {}))


_load_bar_templates()


def _bar_cell_coordstr(arr: np.ndarray, cx: int, cfg: dict) -> str:
    """고정 셀(cx, cfg["y0"]) 내부에서 글자 코어색(center ±tol 박스) 픽셀 좌표 문자열."""
    sub = arr[cfg["y0"]:cfg["y0"] + cfg["h"], cx:cx + 10].astype(int)
    cr, cg, cb = cfg["center"]
    tol = cfg["tol"]
    mask = ((np.abs(sub[:, :, 0] - cr) <= tol)
            & (np.abs(sub[:, :, 1] - cg) <= tol)
            & (np.abs(sub[:, :, 2] - cb) <= tol))
    ys, xs = np.where(mask)
    return "".join(f"{x}{y}" for x, y in sorted(zip(xs.tolist(), ys.tolist())))


def _bar_digit_groups(arr: np.ndarray, stat: str) -> list[str]:
    """고정 앵커에서 step 방향으로 셀을 훑으며 숫자를 인식하고, 연속된 숫자 셀끼리
    묶어 그룹 리스트로 돌려준다. 숫자가 아닌 셀(접두사/":"/"/")은 사전에 없어
    그룹 경계가 된다. 왼쪽으로 읽었으면(step<0) 각 그룹 자릿수를 정방향으로 뒤집는다."""
    cfg = _BAR_CONFIGS[stat]
    digits = _BAR_TEMPLATES[stat]
    anchor, step, bound = cfg["anchor"], cfg["step"], cfg["bound"]

    groups: list[list[str]] = []
    current: list[str] = []
    cx = anchor
    while (cx <= bound) if step > 0 else (cx >= bound):
        d = digits.get(_bar_cell_coordstr(arr, cx, cfg))
        if d is not None:
            current.append(d)
        elif current:
            groups.append(current)
            current = []
            if len(groups) >= 2:  # 현재/최대 두 그룹이면 충분
                break
        cx += step
    if current and len(groups) < 2:
        groups.append(current)

    if step < 0:
        groups = [g[::-1] for g in groups]
    return ["".join(g) for g in groups]


def read_bar_stat(image: Image.Image, stat: str) -> tuple[int | None, int | None]:
    """하단 피통바에서 (현재값, 최대값)을 읽는다. stat = "HP" 또는 "MP".
    인식 실패 시 해당 값은 None."""
    arr = np.array(image.convert("RGB"))
    groups = _bar_digit_groups(arr, stat)
    if len(groups) < 2:
        return None, None
    # MP(왼쪽->오른쪽): [현재, 최대].  HP(오른쪽->왼쪽): [최대, 현재].
    if _BAR_CONFIGS[stat]["step"] > 0:
        current, maximum = int(groups[0]), int(groups[1])
    else:
        maximum, current = int(groups[0]), int(groups[1])
    return current, maximum


def read_hp(img=None) -> int:
    """하단 빨간 HP 피통바에서 현재값을 읽는다. 실패 시 0."""
    global max_hp, current_hp
    if img is None:
        img = screenshot()
    cur, mx = read_bar_stat(img, "HP")
    # 인식 실패(None) 시 직전 값을 유지한다. None 을 그대로 돌려주면 server 의
    # max_hp/hp 비교(None <= 0)에서 TypeError 가 나 루프가 죽어 read 가 멈춘다.
    if cur is not None:
        current_hp = cur
    if mx is not None:
        max_hp = mx
    print(f"[macro] HP 읽기: current={current_hp}, max={max_hp}")
    return current_hp, max_hp


def read_mp(img=None) -> int:
    """하단 파란 MP 피통바에서 현재값을 읽는다. 실패 시 0."""
    global mp_1
    if img is None:
        img = screenshot()
    current, _ = read_bar_stat(img, "MP")
    if current is not None:
        mp_1 = current
    return mp_1


def readAdena() -> int:
    force_set_foreground_window(lineage1_hwnd)
    while True:
        key_press(win32con.VK_F9)
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


def rejectExchange():
    win32api.SetCursorPos((311, 752))
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    arduino_key_press(ord('Y'))
    time.sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')
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


def has_target_in_input() -> str:
    """shift+우클릭으로 타겟 확인 후 입력창 초기화. 타겟 텍스트 반환 (없으면 빈 문자열)"""
    x, y = _mouse_xy
    win32api.SetCursorPos((x, y))
    arduino_mouse_shift_click_right(x, y)
    time.sleep(0.1)
    img = screenshot(hwnd=lineage1_hwnd)
    input_text = readInputText(img)
    arduino_key_down(win32con.VK_CONTROL)
    arduino_key_press(win32con.VK_BACK)
    arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)
    return input_text


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

