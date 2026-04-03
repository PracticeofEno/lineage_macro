import win32gui


def enum_windows_callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))


def get_open_windows():
    windows = []
    win32gui.EnumWindows(enum_windows_callback, windows)
    return windows


if __name__ == "__main__":
    windows = get_open_windows()
    print(f"{'HWND':<12} {'Title'}")
    print("-" * 60)
    for hwnd, title in windows:
        print(f"{hwnd:<12} {title}")
