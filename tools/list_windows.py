import win32gui


def callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))


windows = []
win32gui.EnumWindows(callback, windows)

print(f"{'HWND':<12} {'Title'}")
print("-" * 60)
for hwnd, title in windows:
    print(f"{hwnd:<12} {title}")
