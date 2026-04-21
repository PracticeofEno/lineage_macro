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
import ctypes
from ctypes import windll
from datetime import datetime
from PIL import Image


def _sleep(base: float, jitter: float = 0.2):
    """base 시간에 ±jitter 비율의 랜덤 편차를 더해 sleep한다."""
    duration = base * (1.0 + random.uniform(-jitter, jitter))
    time.sleep(max(0.0, duration))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hangul

_BASE = os.path.dirname(os.path.abspath(__file__))
_CONVERTED_DATA_PATH = os.path.join(_BASE, "converted_data.json")
with open(_CONVERTED_DATA_PATH, encoding="utf-8") as _f:
    _converted_map: dict[str, str] = json.load(_f)

_CONVERTED_EXCHANGE_DATA_PATH = os.path.join(_BASE, "converted_exchange_data.json")
with open(_CONVERTED_EXCHANGE_DATA_PATH, encoding="utf-8") as _f:
    _converted_exchange_map: dict[str, str] = {v: k for k, v in json.load(_f).items()}


def lookup(coord_string: str) -> str | None:
    return _converted_map.get(coord_string)


def lookup_exchange(coord_string: str) -> str | None:
    return _converted_exchange_map.get(coord_string)


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

def read_itemslot_number(image: Image.Image, x: int, y: int) -> str:
    color = (0xbe, 0xbe, 0xbe)
    result = []
    img_width = image.width
    while x < img_width:
        if x + 10 > img_width:
            break
        s = image_to_coord_string(crop(image, x, y, 10, 21), color)
        matched = lookup_exchange(s)
        if matched is None:
            break
        result.append(matched)
        x += 10
    return ''.join(result)


def read_exchange_adena(img=None) -> str:
    global _exchange_nickname_xy
    if img is None:
        img = screenshot()
    if _exchange_nickname_xy is None:
        print("이건 발생할 수 없음! 발생한다면 뭬쳐따리~")
        return ''
    x = 132
    y = _exchange_nickname_xy[1] + 111
    cropped = crop(img, x, y, 200, 21)
    number_string = read_itemslot_number(cropped, 0, 0)
    digits = number_string.replace(',', '')
    try:
        return int(digits)
    except (ValueError, TypeError):
        return 0


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
        _sleep(duration - 0.05)


def arduino_mouse_click_left():
    _arduino_send('CL')


def arduino_mouse_click_right():
    _arduino_send('CR')


def arduino_mouse_shift_click_left(x: int, y: int):
    arduino_mouse_move_to(x, y)
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    _sleep(0.5)
    arduino_mouse_click_left()
    _sleep(0.5)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')


def arduino_mouse_shift_click_right(x: int, y: int):
    arduino_mouse_move_to(x, y)
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    _sleep(0.05)
    arduino_mouse_click_right()
    _sleep(0.05)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')


def arduino_backspace(n: int):
    _arduino_send(f'BS,{n}')


def arduino_alt_tab(n: int = 1):
    """Alt+Tab으로 창 전환한다. n: Tab 횟수 (여러 번으로 원하는 창까지 이동)"""
    _arduino_send(f'KD,{win32con.VK_MENU}')
    _sleep(0.20)  # switcher 뜨는 걸 눈으로 확인하는 반응 시간
    for i in range(n):
        _arduino_send(f'KP,{win32con.VK_TAB}')
        if i < n - 1:
            _sleep(0.28)  # 다음 창으로 넘길 때 잠깐 멈추는 습관
    _sleep(0.15)  # 원하는 창 확인 후 Alt를 뗌
    _arduino_send(f'KU,{win32con.VK_MENU}')


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
    1. "Lineage Classic"으로 시작하는 윈도우를 찾아 lineage1_hwnd 지정 후 (0,0)으로 이동
    2. macro_data.json에서 설정 로드
    """
    global lineage1_hwnd
    global _mouse_key
    global direction_threshold, adena_per_pickup, current_direction, low_count_direction, high_count_direction
    global _TURN_XY

    lineage1_hwnd = _find_lineage_hwnd()
    rect = win32gui.GetWindowRect(lineage1_hwnd)
    win32gui.MoveWindow(lineage1_hwnd, 0, 0, rect[2] - rect[0], rect[3] - rect[1], True)
    print(f"[macro] lineage1_hwnd={lineage1_hwnd} → 위치 (0, 0)")

    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    if role == "server":
        mouse_key = "server_mouse_x_y"
    elif role == "client":
        mouse_key = "client_mouse_x_y"
    else:
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
    find_adena_x_y()
    findExchangeNicknameY()


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


def mouse_click_left():
    arduino_mouse_click_left()
    _sleep(0.3)


def mouse_click_right():
    arduino_mouse_click_right()


def _send_char(ch: str):
    hangul.send_char(ch, get_hwnd())


def _backspace(n: int):
    arduino_backspace(n)


def force_set_foreground_window(hwnd: int):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    windll.user32.keybd_event(0, 0, 0, 0)  # null 입력으로 포그라운드 권한 획득
    win32gui.SetForegroundWindow(hwnd)
    _sleep(0.05)

def _get_cursor_pos() -> tuple[int, int]:
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


_hid_scale_x: float | None = None  # x축 픽셀/HID-unit 비율
_hid_scale_y: float | None = None  # y축 픽셀/HID-unit 비율
_CORRECTION_THRESHOLD = 2           # 이 픽셀 이하면 보정 생략
_MAX_CORRECTIONS = 4                # 최대 보정 횟수

_CALIBRATION_TARGETS = [
    (500, 300), (1000, 500), (200, 700),
    (800, 200), (600, 400), (500, 300),
]


_SPI_GETMOUSE = 0x0003
_SPI_SETMOUSE = 0x0004
_SPI_GETMOUSESPEED = 0x0070
_SPI_SETMOUSESPEED = 0x0071


def calibrate_hid_scale() -> None:
    """HID scale을 학습시키기 위해 여러 좌표를 순서대로 이동한다."""
    old_params = (ctypes.c_int * 3)()
    ctypes.windll.user32.SystemParametersInfoW(_SPI_GETMOUSE, 0, old_params, 0)
    old_speed = ctypes.c_int(0)
    ctypes.windll.user32.SystemParametersInfoW(_SPI_GETMOUSESPEED, 0, ctypes.byref(old_speed), 0)

    no_accel = (ctypes.c_int * 3)(0, 0, 0)
    ctypes.windll.user32.SystemParametersInfoW(_SPI_SETMOUSE, 0, no_accel, 0)
    ctypes.windll.user32.SystemParametersInfoW(_SPI_SETMOUSESPEED, 0, ctypes.c_void_p(10), 0)

    try:
        print("[macro] HID scale 보정 시작...")
        for tx, ty in _CALIBRATION_TARGETS:
            before = _get_cursor_pos()
            arduino_mouse_move_to(tx, ty)
            after = _get_cursor_pos()
            print(f"[calibrate] target=({tx},{ty})  before={before}  after={after}  moved=({after[0]-before[0]},{after[1]-before[1]})")
            time.sleep(0.1)
        if _hid_scale_x is None or _hid_scale_y is None:
            print("[macro] HID scale 보정 실패: 커서 이동이 감지되지 않았습니다.")
        else:
            print(f"[macro] HID scale 보정 완료: sx={_hid_scale_x:.4f}  sy={_hid_scale_y:.4f}")
    finally:
        ctypes.windll.user32.SystemParametersInfoW(_SPI_SETMOUSE, 0, old_params, 0)
        ctypes.windll.user32.SystemParametersInfoW(_SPI_SETMOUSESPEED, 0, ctypes.c_void_p(old_speed.value), 0)


def _wait_cursor_stop(timeout: float = 1.5, poll: float = 0.02, stable_needed: int = 5) -> None:
    """커서가 멈출 때까지 대기"""
    deadline = time.time() + timeout
    start = _get_cursor_pos()
    prev = start
    moved = False
    stable = 0
    while time.time() < deadline:
        time.sleep(poll)
        cur = _get_cursor_pos()
        if not moved and (abs(cur[0] - start[0]) > 1 or abs(cur[1] - start[1]) > 1):
            moved = True
        if moved:
            if abs(cur[0] - prev[0]) <= 1 and abs(cur[1] - prev[1]) <= 1:
                stable += 1
                if stable >= stable_needed:
                    return
            else:
                stable = 0
        prev = cur


def _update_hid_scale(hid_x: int, hid_y: int, moved_x: int, moved_y: int) -> None:
    global _hid_scale_x, _hid_scale_y
    if abs(hid_x) > 5 and abs(moved_x) > 0:
        m = abs(moved_x) / abs(hid_x)
        if 0.1 < m < 20.0:
            _hid_scale_x = m if _hid_scale_x is None else _hid_scale_x * 0.65 + m * 0.35
    if abs(hid_y) > 5 and abs(moved_y) > 0:
        m = abs(moved_y) / abs(hid_y)
        if 0.1 < m < 20.0:
            _hid_scale_y = m if _hid_scale_y is None else _hid_scale_y * 0.65 + m * 0.35


def arduino_mouse_move_rel(dx: int, dy: int):
    _arduino_send(f"RM,{dx},{dy}")


def arduino_mouse_move_to(target_x: int, target_y: int):
    """절대 좌표로 이동. x/y 독립 scale 학습 + 반복 오차 보정."""
    cur_x, cur_y = _get_cursor_pos()
    dx = target_x - cur_x
    dy = target_y - cur_y
    if dx == 0 and dy == 0:
        return

    sx = _hid_scale_x or 1.0
    sy = _hid_scale_y or 1.0
    hid_dx = int(round(dx / sx))
    hid_dy = int(round(dy / sy))
    if hid_dx == 0 and hid_dy == 0:
        return

    _arduino_send(f'RM,{hid_dx},{hid_dy}')
    _wait_cursor_stop()

    prev_x, prev_y = cur_x, cur_y
    prev_hid_x, prev_hid_y = hid_dx, hid_dy

    for _ in range(_MAX_CORRECTIONS):
        actual_x, actual_y = _get_cursor_pos()
        _update_hid_scale(prev_hid_x, prev_hid_y,
                          actual_x - prev_x, actual_y - prev_y)

        err_x = target_x - actual_x
        err_y = target_y - actual_y
        if abs(err_x) <= _CORRECTION_THRESHOLD and abs(err_y) <= _CORRECTION_THRESHOLD:
            break

        sx = _hid_scale_x or 1.0
        sy = _hid_scale_y or 1.0
        corr_x = int(round(err_x / sx))
        corr_y = int(round(err_y / sy))
        if corr_x == 0 and corr_y == 0:
            break

        _arduino_send(f'RM,{corr_x},{corr_y}')
        _wait_cursor_stop()

        prev_x, prev_y = actual_x, actual_y
        prev_hid_x, prev_hid_y = corr_x, corr_y

def shake_mouse_small(count=10, dist=10, delay=0.05):
    for _ in range(count):
        arduino_mouse_move_rel(dist, 0)
        _sleep(delay)
        arduino_mouse_move_rel(-dist, 0)
        _sleep(delay)

def use_potion():
    force_set_foreground_window(lineage1_hwnd)
    _sleep(0.5)
    _arduino_send(f'KP,{win32con.VK_F8}')


def pickup_lineage1(target_nickname: str | None = None):
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    x, y = tuple(data[_mouse_key])
    force_set_foreground_window(lineage1_hwnd)
    arduino_mouse_move_to(x, y)
    _sleep(0.1)

    for attempt in range(4):
        arduino_mouse_shift_click_right(x, y)
        _sleep(0.1)
        img = screenshot(hwnd=lineage1_hwnd)
        input_text = readInputText(img)
        print(f"[macro] 타겟 확인 ({attempt+1}/4): '{input_text}' == '{target_nickname}'?")
        arduino_key_down(win32con.VK_CONTROL)
        arduino_key_press(win32con.VK_BACK)
        arduino_key_up(win32con.VK_CONTROL)
        _sleep(0.1)
        if input_text == target_nickname:
            print("[macro] 타겟 고정 성공")
            break
    else:
        print("[macro] 타겟 고정 실패 - pickup 진행")

    key_press(win32con.VK_F5)
    _sleep(0.1)
    mouse_click_left()
    _sleep(0.1)

def pickup_lineage2(target_nickname: str | None = None):
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    x, y = tuple(data[_mouse_key])
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
        print("[macro] 타겟 고정 실패 - pickup 진행")

    key_press(win32con.VK_F5)
    time.sleep(0.1)
    mouse_click_left()
    time.sleep(0.1)



def checkExchangeRequest(img=None) -> bool:
    if img is None:
        img = screenshot()
    cropped = crop(img, 848, 877, 5, 5)
    arr = np.array(cropped.convert("RGB"))
    mask = (arr[:,:,0] == 0) & (arr[:,:,1] == 0) & (arr[:,:,2] == 0)
    # print(f"[macro] 교환 요청 픽셀 검출: {mask.sum()} / 25")
    return int(mask.sum()) >= 10

def get_brightness(image: Image.Image) -> float:
    """이미지의 평균 밝기(0.0~255.0)를 반환한다."""
    arr = np.array(image.convert('RGB'), dtype=np.float32)
    return float(arr.mean())


def readMp(img=None) -> int:
    if img is None:
        img = screenshot()
    for dx in (0, 5, 10):
        cropped = crop(img, 976 + dx, 96, 100, 21)
        text = read_text(cropped, 0, 0, (0xCC, 0xE3, 0xFF))
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
        _sleep(0.5)


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
    arduino_mouse_move_to(247, 752)
    _sleep(0.5)
    arduino_mouse_click_left()
    _sleep(0.5)
    arduino_key_press(ord('Y'))
    _sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')
    _sleep(0.3)


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


def read_my_adena(img=None) -> int:
    if my_adena_x_y is None:
        raise RuntimeError("my_adena_x_y가 설정되지 않았습니다. find_adena_x_y()를 먼저 호출하세요.")
    if img is None:
        img = screenshot()
    if my_adena_x_y is None:
        find_adena_x_y(img)
    x, y = my_adena_x_y
    arduino_mouse_move_to(x -52, y -27)
    img = screenshot()
    _sleep(0.1)
    cropped = crop(img, x, y, 300, 21)
    text = read_itemslot_number(cropped, 0, 0)
    digits = ''.join(c for c in text if c.isdigit())
    return int(digits) if digits else 0


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
        _sleep(0.5)


_DIRECTION_FUNCS = {
    'north': turn_north, 'northeast': turn_northeast,
    'east': turn_east, 'southeast': turn_southeast,
    'south': turn_south, 'southwest': turn_southwest,
    'west': turn_west, 'northwest': turn_northwest,
}


my_adena_x_y: tuple[int, int] | None = None


def find_adena_x_y(img=None, origin_x: int = 997, origin_y: int = 190,
                   width: int = 50, height: int = 100,
                   min_streak: int = 8) -> tuple[int, int] | None:
    global my_adena_x_y
    if img is None:
        img = screenshot()
    region = crop(img, origin_x, origin_y, width, height)
    arr = np.array(region.convert("RGB"))
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    yellow = (r >= 200) & (g >= 200) & (b <= 100)

    for row_idx in range(yellow.shape[0]):
        streak = 0
        streak_start = None
        for col_idx in range(yellow.shape[1]):
            if yellow[row_idx, col_idx]:
                if streak == 0:
                    streak_start = col_idx
                streak += 1
            else:
                if streak >= min_streak:
                    found = (origin_x + streak_start, origin_y + row_idx)
                    my_adena_x_y = (found[0] + 52, found[1] + 47)
                    return found
                streak = 0
                streak_start = None
        if streak >= min_streak:
            found = (origin_x + streak_start, origin_y + row_idx)
            my_adena_x_y = (found[0] + 52, found[1] + 47)
            return found
    return None

