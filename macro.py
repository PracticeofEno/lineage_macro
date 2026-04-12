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
import imageProcesser

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
    time.sleep(0.05)
    _arduino_send('CL')
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
low_count_direction = 'southeast'
high_count_direction = 'northwest'
exchange_yes_button = (869, 914)  # 교환 수락 Yes 좌표
exchange_no_button = (917, 912)   # 교환 수락 No 좌표


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

    direction_threshold = data["direction_threshold"]
    adena_per_pickup = data["adena_per_pickup"]
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    for d in ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])

    print(f"[macro] mouse_key={mouse_key}")
    print(f"[macro] direction_threshold={direction_threshold}, current={current_direction}, low={low_count_direction}, high={high_count_direction}")
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


def screenshot(filename: str = None, hwnd: int = None) -> str:
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


def pickup_lineage1():
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    x, y = tuple(data[_mouse_key])
    force_set_foreground_window(lineage1_hwnd)
    win32api.SetCursorPos((x, y))
    time.sleep(0.3)
    shake_mouse_small(2, 10)
    key_press(win32con.VK_F5)
    time.sleep(0.3)
    mouse_click_left(x, y)
    time.sleep(1)



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


def readMp(img=None) -> int:
    if img is None:
        img = screenshot()
    for dx in (0, 5, 10):
        cropped = imageProcesser.crop(img, 976 + dx, 96, 100, 21)
        text = imageProcesser.read_text(cropped, 0, 0, (0xCC, 0xE3, 0xFF))
        parts = text.split('/')
        digits = ''.join(c for c in parts[0] if c.isdigit())
        if digits:
            return int(digits)
    return 0


def readAdena() -> int:
    force_set_foreground_window(lineage1_hwnd)
    while True:
        key_press(win32con.VK_F9)
        img = screenshot()
        cropped = imageProcesser.crop(img, 228 + 60 + 5 + 5, 883, 500, 21)
        text = imageProcesser.read_text(cropped, 0, 0, (0xFF, 0xF1, 0xB5))
        if '(' in text and ')' in text:
            inner = text[text.index('(') + 1:text.index(')')]
            digits = inner.replace(' ', '')
            return int(digits) if digits else 0
        time.sleep(0.5)


def readExchangeNickname(img=None):
    if img is None:
        img = screenshot()
    text = imageProcesser.readExchangeNickname(img)
    return text


def monitor_chat():
    prev = None
    while True:
        img = screenshot()
        cropped = imageProcesser.crop(img, 228, 907, 140, 25)
        text = imageProcesser.read_text(cropped, 0, 0, (0xAF, 0xEB, 0xEB))
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

