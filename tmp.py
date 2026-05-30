import os
import time
import threading
import win32gui
import win32ui
import win32con
from ctypes import windll
from PIL import Image

running = False
_counter = 0


def _find_server_hwnd() -> int:
    result = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "server":
            result.append(hwnd)
    win32gui.EnumWindows(callback, None)
    if not result:
        raise RuntimeError("'server' 윈도우를 찾을 수 없습니다.")
    return result[0]


def _screenshot(hwnd: int) -> Image.Image:
    rect = win32gui.GetWindowRect(hwnd)
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]

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

    return img.crop((0, 0, img.width - 16, img.height - 41))


def _capture_loop():
    global _counter
    os.makedirs("tmp_screenshots", exist_ok=True)
    hwnd = _find_server_hwnd()
    print(f"[tmp] server hwnd: {hwnd}")
    while running:
        img = _screenshot(hwnd)
        path = os.path.join("tmp_screenshots", f"{_counter}.png")
        img.save(path)
        print(f"[tmp] 저장: {path}")
        _counter += 1
        time.sleep(3)


if __name__ == "__main__":
    print("1=캡처 시작, 2=캡처 중지, q=종료")
    capture_thread = None
    while True:
        cmd = input("> ").strip()
        if cmd == "1":
            if capture_thread and capture_thread.is_alive():
                print("[tmp] 이미 실행 중")
            else:
                running = True
                _counter = 0
                capture_thread = threading.Thread(target=_capture_loop, daemon=True)
                capture_thread.start()
                print("[tmp] 캡처 시작")
        elif cmd == "2":
            running = False
            print("[tmp] 캡처 중지")
        elif cmd == "q":
            running = False
            break
