import threading
import time
import win32gui
import win32con
import macro


def enum_windows_callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))


def get_open_windows():
    windows = []
    win32gui.EnumWindows(enum_windows_callback, windows)
    return windows


def select_hwnd():
    windows = get_open_windows()
    print(f"{'#':<4} {'HWND':<12} {'Title'}")
    print("-" * 60)
    for i, (hwnd, title) in enumerate(windows):
        print(f"{i:<4} {hwnd:<12} {title}")

    idx = int(input("\n번호 선택: "))
    hwnd, title = windows[idx]
    macro.set_hwnd(hwnd)


if __name__ == "__main__":
    macro.set_hwnd(4459766)
    macro.move_window(0, 0)

    print("\n명령어: 1=클릭, 2=스크린샷, 3=모든 문자 전송, q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            break
        elif cmd == "1":
            macro.focus_window()
            macro.mouse_click_left(1015, 970)
            print("(1015, 970) 클릭")
        elif cmd == "2":
            img = macro.screenshot()
            img.save("image/screenshot.png")
        elif cmd == "3":
            macro.send_all_chars()
