
import os
import json
from numpy import char
import win32con
import win32com
import win32api
import win32gui
import win32ui
import time
from ctypes import windll
from PIL import Image
import numpy as np
import socket as _socket

from tools import hangul

_BASE = os.path.dirname(os.path.abspath(__file__))
_CONVERTED_DATA_PATH = os.path.join(_BASE, "converted_data.json")
with open(_CONVERTED_DATA_PATH, encoding="utf-8") as _f:
    _converted_map: dict[str, str] = json.load(_f)

# ── Arduino Proxy 연결 ────────────────────────────────────────────────────────
# arduino_proxy.py 가 127.0.0.1:9998 에서 실행 중이어야 한다.
_PROXY_HOST = '127.0.0.1'
_PROXY_PORT = 9998
_proxy_conn: _socket.socket | None = None

_SHIFT_CHAR_MAP = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '<': ',', '>': '.', '?': '/',
    '~': '`',
}


def screenshot(hwnd: int = None) -> Image.Image:
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
    return img

def crop(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    return image.crop((x, y, x + width, y + height))

def image_to_coord_string(image: Image.Image, color: tuple) -> str:
    arr = np.array(image.convert("RGB"))
    r, g, b = color
    mask = (arr[:,:,0] == r) & (arr[:,:,1] == g) & (arr[:,:,2] == b)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)

def lookup(coord_string: str) -> str | None:
    return _converted_map.get(coord_string)

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

def _proxy_connect():
    global _proxy_conn
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.connect((_PROXY_HOST, _PROXY_PORT))
    _proxy_conn = s
    print(f"[macro] Arduino proxy 연결됨: {_PROXY_HOST}:{_PROXY_PORT}")

def _arduino_send(cmd: str) -> str:
    """명령을 proxy 에 전송하고 Arduino 의 응답을 반환한다."""
    global _proxy_conn
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

def set_forground_window(hwnd: int):
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.1)

def find_hwnd(title: str) -> int:
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    if not result:
        raise RuntimeError(f"'{title}' 윈도우를 찾을 수 없습니다.")
    return result[0]

def use_potion():
    _arduino_send(f'KP,{win32con.VK_F8}')

def findExchangeNicknameY(img=None) -> tuple[int, int] | None:
    """y=480에서 50까지 스캔하며 닉네임 텍스트가 처음 발견되는 (x, y) 좌표를 반환한다."""
    w, h = 140, 24
    color = (255, 255, 255)
    for y in range(480, 49, -1):
        for x in range(107, 56, -5):
            cropped = crop(img, x, y, w, h)
            text = read_text(cropped, 0, 0, color)
            if text:
                return (x, y)
    return None

def read_exchange_nickname_img(screenshot: Image.Image, y: int = 292) -> str:
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

def arduino_mouse_shift_click_left(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    time.sleep(0.5)
    _arduino_send('CL')
    time.sleep(0.5)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')

def arduino_backspace(n: int):
    _arduino_send(f'BS,{n}')

def arduino_key_down(vk: int):
    _arduino_send(f'KD,{vk}')


def arduino_key_up(vk: int):
    _arduino_send(f'KU,{vk}')

def arduino_mouse_click_left(x: int, y: int):
    _arduino_send('CL')

def arduino_mouse_click_right(x: int, y: int):
    _arduino_send('CR')

def arduino_key_press(vk: int, duration: float = 0.05):
    """duration 이 필요 없는 경우 Arduino 내부에서 30 ms 딜레이를 처리한다."""
    _arduino_send(f'KP,{vk}')
    if duration > 0.05:
        time.sleep(duration - 0.05)

def arduino_mouse_shift_click_right(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    time.sleep(0.05)
    _arduino_send('CR')
    time.sleep(0.05)
    _arduino_send(f'KU,{win32con.VK_SHIFT}')

def readInputText(img=None) -> str:
    if img is None:
        img = screenshot()
    return read_text(img, 249, 933, (0xff, 0xff, 0xff)).replace('|', '')

def get_brightness(image: Image.Image) -> float:
    """이미지의 평균 밝기(0.0~255.0)를 반환한다."""
    arr = np.array(image.convert('RGB'), dtype=np.float32)
    return float(arr.mean())

def readAdena() -> int:
    while True:
        arduino_key_press(win32con.VK_F9)
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

def readMp(img) -> int:
    for dx in (0, 5, 10):
        cropped = crop(img, 976 + dx, 96, 100, 21)
        text = read_text(cropped, 0, 0, (0xCC, 0xE3, 0xFF))
        parts = text.split('/')
        digits = ''.join(c for c in parts[0] if c.isdigit())
        if digits:
            return int(digits)
    return 0