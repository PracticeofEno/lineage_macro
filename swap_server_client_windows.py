import argparse
import time

import win32gui


def list_visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return windows


def find_windows_by_title(title: str) -> list[int]:
    return [hwnd for hwnd, window_title in list_visible_windows() if window_title == title]


def select_single(title: str, force_first: bool) -> int:
    matches = find_windows_by_title(title)
    if not matches:
        raise RuntimeError(f"window not found: {title!r}")
    if len(matches) > 1 and not force_first:
        raise RuntimeError(
            f"multiple windows found for {title!r}: {matches}. "
            "Close duplicates or rerun with --force-first."
        )
    return matches[0]


def print_targets(server_hwnd: int, client_hwnd: int) -> None:
    print("[swap] targets:")
    print(f"  server hwnd={server_hwnd} title={win32gui.GetWindowText(server_hwnd)!r}")
    print(f"  client hwnd={client_hwnd} title={win32gui.GetWindowText(client_hwnd)!r}")


def swap_titles(server_title: str, client_title: str, force_first: bool, dry_run: bool) -> None:
    server_hwnd = select_single(server_title, force_first)
    client_hwnd = select_single(client_title, force_first)
    if server_hwnd == client_hwnd:
        raise RuntimeError("server and client resolved to the same window")

    print_targets(server_hwnd, client_hwnd)
    if dry_run:
        print("[swap] dry-run only; no titles changed")
        return

    temp_title = f"__swap_temp_{int(time.time() * 1000)}__"
    win32gui.SetWindowText(server_hwnd, temp_title)
    win32gui.SetWindowText(client_hwnd, server_title)
    win32gui.SetWindowText(server_hwnd, client_title)

    print("[swap] complete:")
    print(f"  hwnd={server_hwnd} -> {win32gui.GetWindowText(server_hwnd)!r}")
    print(f"  hwnd={client_hwnd} -> {win32gui.GetWindowText(client_hwnd)!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Swap exact 'server' and 'client' window titles.")
    parser.add_argument("--server-title", default="server", help="Current server window title.")
    parser.add_argument("--client-title", default="client", help="Current client window title.")
    parser.add_argument("--force-first", action="store_true", help="Use the first match if duplicate titles exist.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected windows without changing titles.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        swap_titles(args.server_title, args.client_title, args.force_first, args.dry_run)
    except RuntimeError as exc:
        print(f"[swap] error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
