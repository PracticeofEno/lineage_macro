import os
import time
from datetime import datetime

import win32gui

import macro


def find_hwnd_by_title(title: str) -> int:
    """타이틀이 정확히 일치하는 보이는 윈도우의 HWND를 반환한다."""
    result = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            result.append(hwnd)

    win32gui.EnumWindows(callback, None)
    if not result:
        raise RuntimeError(f"'{title}' 타이틀을 가진 윈도우를 찾을 수 없습니다.")
    return result[0]


def main():
    hwnd = find_hwnd_by_title("server")
    macro.set_hwnd(hwnd)

    save_dir = "screenshots"
    os.makedirs(save_dir, exist_ok=True)

    print("[tmp] 1초마다 스크린샷 저장 시작 (Ctrl+C로 중단)")
    while True:
        img = macro.screenshot()
        filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png"
        path = os.path.join(save_dir, filename)
        img.save(path)
        print(f"[tmp] 저장됨: {path}")
        time.sleep(1)


if __name__ == "__main__":
    main()
