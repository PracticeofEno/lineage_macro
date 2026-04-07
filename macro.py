import os
import sys
import win32api
import win32con
import win32gui
import win32ui
import time
import serial
import numpy as np
from ctypes import windll
from datetime import datetime
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import hangul
import imageProcesser
import ocr

lineage1_hwnd = None
lineage2_hwnd = None
arduino = serial.Serial('COM5', 115200, timeout=1)


# ── Arduino HID 래퍼 ──────────────────────────────────────────────────────────
# 기존 winapi 함수(key_down / key_up / mouse_click_left 등)와 동일한 인터페이스.
# Python 쪽은 Windows VK 코드를 그대로 넘기면 Arduino 가 HID 코드로 변환한다.

def _arduino_send(cmd: str) -> str:
    """명령 전송 후 Arduino 의 'OK' 응답을 기다린다."""
    arduino.write((cmd + '\n').encode())
    return arduino.readline().decode().strip()


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
    win32api.SetCursorPos((x, y))
    _arduino_send('CL')


def arduino_mouse_click_right(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send('CR')


def arduino_mouse_shift_click_left(x: int, y: int):
    win32api.SetCursorPos((x, y))
    _arduino_send(f'KD,{win32con.VK_SHIFT}')
    _arduino_send('CL')
    _arduino_send(f'KU,{win32con.VK_SHIFT}')


def arduino_backspace(n: int):
    _arduino_send(f'BS,{n}')


# ── Turn (방향 이동) ───────────────────────────────────────────────────────────
_TURN_CX, _TURN_CY = 648, 378   # 기준 중심 좌표
_TURN_R = 150                    # 클릭 반경 (픽셀)
_TURN_D = int(_TURN_R * 0.7071) # 대각선 거리 (R * cos45°)

def turn_north():
    arduino_mouse_shift_click_left(_TURN_CX, _TURN_CY - _TURN_R)

def turn_northeast():
    arduino_mouse_shift_click_left(_TURN_CX + _TURN_D, _TURN_CY - _TURN_D)

def turn_east():
    arduino_mouse_shift_click_left(839, 405)

def turn_southeast():
    arduino_mouse_shift_click_left(_TURN_CX + _TURN_D, _TURN_CY + _TURN_D)

def turn_south():
    arduino_mouse_shift_click_left(_TURN_CX, _TURN_CY + _TURN_R)

def turn_southwest():
    arduino_mouse_shift_click_left(_TURN_CX - _TURN_D, _TURN_CY + _TURN_D)

def turn_west():
    arduino_mouse_shift_click_left(436, 407)

def turn_northwest():
    arduino_mouse_shift_click_left(_TURN_CX - _TURN_D, _TURN_CY - _TURN_D)


def arduino_init_cursor():
    """커서를 화면 (0, 0) 으로 초기화한다. 프로그램 시작 시 한 번 호출 권장."""
    _arduino_send('INIT')
lineage1_mouse_x_y = None
lineage2_mouse_x_y = None
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


def init_lineage_windows():
    global lineage1_hwnd, lineage2_hwnd
    result = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).startswith("Lineage Classic"):
            result.append(hwnd)
    win32gui.EnumWindows(callback, None)
    if len(result) < 2:
        raise RuntimeError(f"'Lineage Classic'으로 시작하는 윈도우가 2개 필요하지만 {len(result)}개만 찾았습니다.")
    result.sort(key=lambda h: win32gui.GetWindowText(h))
    lineage1_hwnd = result[0]
    lineage2_hwnd = result[1]
    for hwnd, (x, y) in [(lineage1_hwnd, (0, 0)), (lineage2_hwnd, (637, 0))]:
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
    print(f"[macro] lineage1_hwnd={lineage1_hwnd} ({win32gui.GetWindowText(lineage1_hwnd)})")
    print(f"[macro] lineage2_hwnd={lineage2_hwnd} ({win32gui.GetWindowText(lineage2_hwnd)})")


def init_mouse_x_y(pos1: tuple, pos2: tuple):
    global lineage1_mouse_x_y, lineage2_mouse_x_y
    lineage1_mouse_x_y = pos1
    lineage2_mouse_x_y = pos2
    print(f"[macro] lineage1_mouse_x_y={lineage1_mouse_x_y}")
    print(f"[macro] lineage2_mouse_x_y={lineage2_mouse_x_y}")




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


def screenshot(filename: str = None) -> str:
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


def pickup_lineage1():
    win32gui.SetForegroundWindow(lineage1_hwnd)
    time.sleep(0.05)
    key_press(win32con.VK_F5)
    x, y = lineage1_mouse_x_y
    mouse_click_left(x, y)


def pickup_lineage2():
    win32gui.SetForegroundWindow(lineage2_hwnd)
    time.sleep(0.05)
    key_press(win32con.VK_F5)
    x, y = lineage2_mouse_x_y
    mouse_click_left(x, y)


def checkExchangeRequest() -> bool:
    img = screenshot()
    r, g, b = img.getpixel((848, 877))
    print(f"[macro] 교환 요청 픽셀 RGB: ({r}, {g}, {b})")
    return (r, g, b) == (0, 0, 0)


def get_brightness(image: Image.Image) -> float:
    """이미지의 평균 밝기(0.0~255.0)를 반환한다."""
    arr = np.array(image.convert('RGB'), dtype=np.float32)
    return float(arr.mean())


def readMp() -> int:
    img = screenshot()
    cropped = imageProcesser.crop(img, 717, 667, 100, 21)
    results = ocr.ocr(cropped, ['en'])
    text = ' '.join(t for _, t, _ in results)
    if '/' not in text:
        return 0
    before_slash = text.split('/')[0].strip()
    return int(''.join(c for c in before_slash if c.isdigit()) or 0)


def readAdena() -> int:
    win32gui.SetForegroundWindow(lineage1_hwnd)
    win32api.SetCursorPos((1017, 82))
    time.sleep(1)
    img = screenshot()
    cropped = imageProcesser.crop(img, 1043, 105, 177, 21)
    return imageProcesser.readAdena(cropped)


def readExchangeNickname(img=None):
    if img is None:
        img = screenshot()
    text = imageProcesser.readExchangeNickname(img)
    print(text)
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


def accept_exchange_and_track_adena():
    while not checkExchangeRequest():
        time.sleep(0.5)

    win32gui.SetForegroundWindow(lineage1_hwnd)
    time.sleep(0.5)

    adena_before = readAdena()
    time.sleep(0.5)

    key_press(ord('Y'))
    time.sleep(0.1)
    _arduino_send(f'KP,{win32con.VK_RETURN}')  # Enter 키
    time.sleep(1)

    prev_brightness = None
    while True:
        img = screenshot()
        text = readExchangeNickname(img)
        if not text:
            break

        slot = imageProcesser.crop(img, 241, 360, 30, 30)
        brightness = get_brightness(slot)
        print(f"[macro] 슬롯 밝기: {brightness:.2f}")

        if prev_brightness is not None and brightness != prev_brightness:
            win32api.SetCursorPos((248, 585))
            time.sleep(0.5)
            _arduino_send('CL')
            time.sleep(0.5)
            key_press(ord('Y'))
            time.sleep(0.1)
            _arduino_send(f'KP,{win32con.VK_RETURN}')

        prev_brightness = brightness
        time.sleep(0.5)

    adena_after = readAdena()
    received = adena_after - adena_before
    print(f"[macro] 교환 완료: {adena_before} -> {adena_after} (+{received})")
    return received


def send_all_chars(interval: float = 0.2, batch_size: int = 25):
    """a-z, A-Z, 특수문자는 1글자씩 crop(10px), 한글은 batch_size씩 묶어 20px씩 개별 crop 저장."""
    from convert_show import all_chars

    os.makedirs("data2", exist_ok=True)
    focus_window()

    ascii_chars  = [ch for ch in all_chars() if ord(ch) <= 0x7F]
    hangul_chars = [ch for ch in all_chars() if ord(ch) > 0x7F]

    # 1단계: ASCII (a-z, A-Z, 특수문자) — 1글자씩, crop 10px
    # for i, ch in enumerate(ascii_chars):
    #     print("[macro] 문자 전송:", ch)
    #     _send_char(ch)
    #     time.sleep(interval)

    #     img = screenshot()
    #     cropped = imageProcesser.crop(img, 249, 933, 10, 24)
    #     save_path = os.path.join("data2", f"{i}.png")
    #     cropped.save(save_path)
    #     print(f"[macro] 저장됨: {save_path}")

    #     _backspace(1)
    #     time.sleep(1)

    # 2단계: 한글 — batch_size씩 입력 후 20px 간격으로 개별 crop
    for batch_idx in range(0, len(hangul_chars), batch_size):
        batch = hangul_chars[batch_idx:batch_idx + batch_size]

        for ch in batch:
            print("[macro] 문자 전송:", ch)
            _send_char(ch)
            time.sleep(interval)

        img = screenshot()
        for idx, ch in enumerate(batch):
            cropped = imageProcesser.crop(img, 249 + idx * 20, 933, 20, 24)
            save_path = os.path.join("data2", f"{ch}.png")
            cropped.save(save_path)
        print(f"[macro] 저장됨: {''.join(batch)} ({len(batch)}글자)")

        _backspace(len(batch))
        time.sleep(1)
