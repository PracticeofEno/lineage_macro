import win32api
import win32con
import win32gui
import time

_hwnd = None


def set_hwnd(hwnd: int):
    global _hwnd
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"유효하지 않은 HWND: {hwnd}")
    _hwnd = hwnd
    print(f"[macro] HWND 설정됨: {hwnd} ({win32gui.GetWindowText(hwnd)})")


def get_hwnd() -> int:
    if _hwnd is None:
        raise RuntimeError("HWND가 설정되지 않았습니다. set_hwnd()를 먼저 호출하세요.")
    return _hwnd


def send_message(msg: int, wparam: int = 0, lparam: int = 0):
    win32api.SendMessage(get_hwnd(), msg, wparam, lparam)


def post_message(msg: int, wparam: int = 0, lparam: int = 0):
    win32api.PostMessage(get_hwnd(), msg, wparam, lparam)


def key_down(vk: int):
    post_message(win32con.WM_KEYDOWN, vk, 0)


def key_up(vk: int):
    post_message(win32con.WM_KEYUP, vk, 0)


def key_press(vk: int, duration: float = 0.05):
    key_down(vk)
    time.sleep(duration)
    key_up(vk)


def mouse_click_left(x: int, y: int):
    lparam = (y << 16) | (x & 0xFFFF)
    post_message(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.05)
    post_message(win32con.WM_LBUTTONUP, 0, lparam)


def mouse_click_right(x: int, y: int):
    lparam = (y << 16) | (x & 0xFFFF)
    post_message(win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lparam)
    time.sleep(0.05)
    post_message(win32con.WM_RBUTTONUP, 0, lparam)
