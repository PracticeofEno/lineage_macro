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
import serial
import numpy as np
from ctypes import windll
from datetime import datetime
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
lineage1_mouse_x_y = None
lineage2_mouse_x_y = None
current_direction = 'north'
available_count_1 = 0
available_count_2 = 0
mp_1 = 0
mp_2 = 0
direction_threshold = 4
adena_per_pickup = 150
low_count_direction = 'southeast'
high_count_direction = 'northwest'
_last_type_string_time = 0
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


def init_mouse_x_y():
    global lineage1_mouse_x_y, lineage2_mouse_x_y
    global direction_threshold, adena_per_pickup, current_direction, low_count_direction, high_count_direction
    global _TURN_XY
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    lineage1_mouse_x_y = tuple(data["lineage1_mouse_x_y"])
    lineage2_mouse_x_y = tuple(data["lineage2_mouse_x_y"])
    direction_threshold = data["direction_threshold"]
    adena_per_pickup = data["adena_per_pickup"]
    current_direction = data["current_direction"]
    low_count_direction = data["low_count_direction"]
    high_count_direction = data["high_count_direction"]
    for d in ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']:
        _TURN_XY[d] = tuple(data[f"turn_{d}_xy"])
    print(f"[macro] lineage1_mouse_x_y={lineage1_mouse_x_y}")
    print(f"[macro] lineage2_mouse_x_y={lineage2_mouse_x_y}")
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


def pickup_lineage1():
    force_set_foreground_window(lineage1_hwnd)
    time.sleep(0.3)
    key_press(win32con.VK_F5)
    time.sleep(0.3)
    x, y = lineage1_mouse_x_y
    mouse_click_left(x, y)
    time.sleep(1)


def pickup_lineage2():
    force_set_foreground_window(lineage2_hwnd)
    time.sleep(0.3)
    key_press(win32con.VK_F5)
    time.sleep(0.3)
    x, y = lineage2_mouse_x_y
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
    cropped = imageProcesser.crop(img, 715, 667, 100, 21)
    results = ocr.ocr(cropped, ['en'])
    text = ' '.join(t for _, t, _ in results)
    print(f"[macro] MP OCR 결과: '{text}'")
    if '/' not in text:
        return 0
    before_slash = text.split('/')[0].strip()
    return int(''.join(c for c in before_slash if c.isdigit()) or 0)


def readAdena() -> int:
    force_set_foreground_window(lineage1_hwnd)
    win32api.SetCursorPos((1017, 82))
    time.sleep(1)
    img = screenshot()
    cropped = imageProcesser.crop(img, 1043, 105, 177, 21)
    return imageProcesser.readAdena(cropped)


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


def accept_exchange_and_track_adena():
    import main
    global available_count_1, available_count_2, mp_1, mp_2

    # 닉네임이 읽힐 때까지 F7 입력
    while main.running:
        # 방향 조정
        img = screenshot(hwnd=lineage1_hwnd)
        img2 = screenshot(hwnd=lineage2_hwnd)
        _mp1 = readMp(img)
        _mp2 = readMp(img2)
        if _mp1 != 0:
            mp_1 = _mp1
        if _mp2 != 0:
            mp_2 = _mp2
        available_count_1 = int(mp_1 // 20)
        available_count_2 = int(mp_2 // 20)
        total_count = available_count_1 + available_count_2
        print(total_count, available_count_1, available_count_2, mp_1, mp_2, direction_threshold)
        if total_count < direction_threshold:
            print("current_direction:", current_direction
                  , "low_count_direction:", low_count_direction
                  , "high_count_direction:", high_count_direction)
            if current_direction != low_count_direction:
                force_set_foreground_window(lineage1_hwnd)
                _DIRECTION_FUNCS[low_count_direction]()
                time.sleep(1)
            return
        else:
            print("current_direction:", current_direction
                  , "low_count_direction:", low_count_direction
                  , "high_count_direction:", high_count_direction)
            print(current_direction != high_count_direction)
            if current_direction != high_count_direction:
                force_set_foreground_window(lineage1_hwnd)
                time.sleep(1)
                _DIRECTION_FUNCS[high_count_direction]()
                time.sleep(1)
        global _last_type_string_time
        if time.time() - _last_type_string_time >= 5:
            arduino_type_string(f"\\f2 방당 {adena_per_pickup} \\f= {total_count}방 가능")
            _last_type_string_time = time.time()
        img = screenshot()
        nickname = readExchangeNickname(img)
        if nickname:
            greeted_nickname = nickname
            arduino_type_string(f"최대 {total_count}방 입니다! 확인!")
            break
        _arduino_send(f'KP,{win32con.VK_F7}')  # F7 HID: 0x40
        time.sleep(0.5)

    # 2. 최초 1번 아데나 읽기
    adena_before = readAdena()

    # 3. 밝기 모니터링
    prev_brightness = None
    brightness_changed = False
    while main.running:
        img = screenshot()
        nickname = readExchangeNickname(img)
        if not nickname:
            break  # 5. 밝기 바뀌기 전 닉네임 사라지면 종료

        slot = imageProcesser.crop(img, 241, 360, 30, 30)
        brightness = get_brightness(slot)
        print(f"[macro] 슬롯 밝기: {brightness:.2f}")

        if prev_brightness is not None and brightness != prev_brightness:
            brightness_changed = True
            win32api.SetCursorPos((248, 585))
            time.sleep(0.5)
            _arduino_send('CL')
            time.sleep(0.5)
            key_press(ord('Y'))
            time.sleep(0.1)
            _arduino_send(f'KP,{win32con.VK_RETURN}')

        prev_brightness = brightness
        time.sleep(0.5)

    # 6. 밝기가 바뀐 후 종료된 경우 받은 아데나 계산
    if brightness_changed:
        adena_after = readAdena()
        received = adena_after - adena_before
        print(f"[macro] 교환 완료: {adena_before} -> {adena_after} (+{received})")

        pickup_count = int(received // adena_per_pickup)
        print(f"[macro] 픽업 횟수: {pickup_count}")
        for _ in range(pickup_count):
            if available_count_1 >= available_count_2:
                available_count_1 -= 1
                mp_1 -= 20
                pickup_lineage1()
            else:
                available_count_2 -= 1
                mp_2 -= 20
                pickup_lineage2()
            time.sleep(1)

        if win32gui.GetForegroundWindow() != lineage1_hwnd:
            force_set_foreground_window(lineage1_hwnd)
        time.sleep(0.5)
        arduino_type_string(f"{greeted_nickname}님 고맙습니다~!")
        return received