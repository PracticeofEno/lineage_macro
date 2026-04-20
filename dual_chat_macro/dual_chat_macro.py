from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from ctypes import windll
from dataclasses import dataclass

import win32con
import win32gui

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
TOOLS_DIR = os.path.join(PROJECT_DIR, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import hangul

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "dual_chat_macro.json")
VALID_ROLES = ("server", "client")
VK_HANGUL = 0x15

_SHIFT_CHAR_MAP = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
    "~": "`",
}


@dataclass(slots=True)
class TargetConfig:
    role: str
    window_title_prefix: str
    messages: list[str]


@dataclass(slots=True)
class DualChatConfig:
    proxy_host: str
    proxy_port: int
    starts_in_korean_mode: bool
    send_enter: bool
    cycle_interval_seconds: float
    switch_delay_seconds: float
    post_send_delay_seconds: float
    order: list[str]
    targets: dict[str, TargetConfig]


def _sanitize_messages(values: list[object]) -> list[str]:
    messages: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            messages.append(text)
    return messages


def load_config(path: str) -> DualChatConfig:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    raw_targets = raw.get("targets", {})
    targets: dict[str, TargetConfig] = {}
    for role in VALID_ROLES:
        role_raw = raw_targets.get(role, {}) if isinstance(raw_targets, dict) else {}
        targets[role] = TargetConfig(
            role=role,
            window_title_prefix=str(role_raw.get("window_title_prefix", role)).strip() or role,
            messages=_sanitize_messages(role_raw.get("messages", [])),
        )

    raw_order = raw.get("order", list(VALID_ROLES))
    order = [str(role).strip().casefold() for role in raw_order if str(role).strip().casefold() in VALID_ROLES]
    if not order:
        order = list(VALID_ROLES)

    return DualChatConfig(
        proxy_host=str(raw.get("proxy_host", "127.0.0.1")).strip() or "127.0.0.1",
        proxy_port=int(raw.get("proxy_port", 9998)),
        starts_in_korean_mode=bool(raw.get("starts_in_korean_mode", True)),
        send_enter=bool(raw.get("send_enter", True)),
        cycle_interval_seconds=float(raw.get("cycle_interval_seconds", 5.0)),
        switch_delay_seconds=float(raw.get("switch_delay_seconds", 0.15)),
        post_send_delay_seconds=float(raw.get("post_send_delay_seconds", 0.25)),
        order=order,
        targets=targets,
    )


class ArduinoProxyClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except OSError:
            pass
        self._conn = None

    def _connect_unlocked(self) -> None:
        conn = socket.create_connection((self.host, self.port), timeout=3)
        conn.settimeout(3)
        self._conn = conn

    def _readline_unlocked(self) -> str:
        if self._conn is None:
            raise OSError("proxy not connected")

        buf = b""
        while b"\n" not in buf:
            chunk = self._conn.recv(256)
            if not chunk:
                raise OSError("proxy closed connection")
            buf += chunk
        return buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()

    def command(self, cmd: str) -> str:
        with self._lock:
            if self._conn is None:
                self._connect_unlocked()

            try:
                assert self._conn is not None
                self._conn.sendall((cmd + "\n").encode("utf-8"))
                return self._readline_unlocked()
            except OSError:
                self._close_unlocked()
                self._connect_unlocked()
                assert self._conn is not None
                self._conn.sendall((cmd + "\n").encode("utf-8"))
                return self._readline_unlocked()

    def key_down(self, vk: int) -> None:
        self._expect_ok(f"KD,{vk}")

    def key_up(self, vk: int) -> None:
        self._expect_ok(f"KU,{vk}")

    def key_press(self, vk: int) -> None:
        self._expect_ok(f"KP,{vk}")

    def _expect_ok(self, cmd: str) -> None:
        resp = self.command(cmd)
        if resp != "OK":
            raise RuntimeError(f"Arduino proxy error for {cmd}: {resp}")


class WindowTarget:
    def __init__(self, title_prefix: str):
        self._title_prefix = title_prefix
        self._title_prefix_lower = title_prefix.casefold()
        self._hwnd: int | None = None

    def resolve(self) -> tuple[int, str]:
        if self._hwnd is not None and win32gui.IsWindow(self._hwnd):
            title = win32gui.GetWindowText(self._hwnd)
            if title and title.casefold().startswith(self._title_prefix_lower):
                return self._hwnd, title

        matches: list[tuple[int, str]] = []

        def callback(hwnd: int, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            if title.casefold().startswith(self._title_prefix_lower):
                matches.append((hwnd, title))

        win32gui.EnumWindows(callback, None)
        if not matches:
            raise RuntimeError(f"'{self._title_prefix}' window not found")

        exact = [entry for entry in matches if entry[1].casefold() == self._title_prefix_lower]
        hwnd, title = exact[0] if exact else matches[0]
        self._hwnd = hwnd
        return hwnd, title


def focus_window(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    windll.user32.keybd_event(0, 0, 0, 0)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.05)


class ChatTyper:
    def __init__(self, proxy: ArduinoProxyClient, starts_in_korean_mode: bool, send_enter: bool):
        self._proxy = proxy
        self._starts_in_korean_mode = starts_in_korean_mode
        self._send_enter = send_enter

    def type_text(self, text: str) -> None:
        current_korean_mode = self._starts_in_korean_mode

        def set_mode(need_korean: bool) -> None:
            nonlocal current_korean_mode
            if current_korean_mode == need_korean:
                return
            self._proxy.key_press(VK_HANGUL)
            current_korean_mode = need_korean

        for ch in text:
            is_korean = "\uAC00" <= ch <= "\uD7A3"

            if ch == " ":
                self._proxy.key_press(win32con.VK_SPACE)
            elif is_korean:
                set_mode(True)
                self._type_hangul(ch)
            elif ch.isalpha():
                set_mode(False)
                vk = ord(ch.upper())
                if ch.isupper():
                    self._proxy.key_down(win32con.VK_SHIFT)
                    self._proxy.key_press(vk)
                    self._proxy.key_up(win32con.VK_SHIFT)
                else:
                    self._proxy.key_press(vk)
            elif ch.isdigit():
                set_mode(False)
                self._proxy.key_press(ord(ch))
            elif ch in _SHIFT_CHAR_MAP:
                set_mode(False)
                self._proxy.key_down(win32con.VK_SHIFT)
                self._proxy.key_press(ord(_SHIFT_CHAR_MAP[ch]))
                self._proxy.key_up(win32con.VK_SHIFT)
            else:
                set_mode(False)
                self._proxy.key_press(ord(ch))

        if current_korean_mode != self._starts_in_korean_mode:
            self._proxy.key_press(VK_HANGUL)

        if self._send_enter:
            self._proxy.key_press(win32con.VK_RETURN)

    def _type_hangul(self, ch: str) -> None:
        cho, jung, jong = hangul.decompose_hangul(ch)
        self._type_jamo(cho)
        self._type_jamo(jung)
        if jong:
            self._type_jamo(jong)
        self._proxy.key_press(win32con.VK_RIGHT)

    def _type_jamo(self, jamo: str) -> None:
        if jamo in hangul.COMPOUND_JAMO:
            for part in hangul.COMPOUND_JAMO[jamo]:
                self._type_jamo(part)
            return

        key, shift = hangul.JAMO_KEY_MAP[jamo]
        vk = ord(key)
        if shift:
            self._proxy.key_down(win32con.VK_SHIFT)
        self._proxy.key_press(vk)
        if shift:
            self._proxy.key_up(win32con.VK_SHIFT)


class AlternatingRepeater:
    def __init__(self, send_func):
        self._send_func = send_func
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, order: list[str], messages_by_role: dict[str, list[str]], interval_seconds: float) -> None:
        if self.running:
            raise RuntimeError("repeat loop is already running")
        if interval_seconds <= 0:
            raise RuntimeError("interval must be greater than 0")

        active_roles = [role for role in order if messages_by_role.get(role)]
        if not active_roles:
            raise RuntimeError("no messages configured for repeat loop")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(active_roles, {role: list(messages_by_role[role]) for role in active_roles}, interval_seconds),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()
        assert self._thread is not None
        self._thread.join()
        self._thread = None

    def _run(self, order: list[str], messages_by_role: dict[str, list[str]], interval_seconds: float) -> None:
        message_indices = {role: 0 for role in order}
        order_index = 0

        while not self._stop_event.is_set():
            role = order[order_index]
            messages = messages_by_role[role]
            message = messages[message_indices[role]]

            try:
                self._send_func(role, message)
            except Exception as exc:
                print(f"[chat] repeat error ({role}): {exc}")

            message_indices[role] = (message_indices[role] + 1) % len(messages)
            order_index = (order_index + 1) % len(order)

            if self._stop_event.wait(interval_seconds):
                break


class DualChatMacro:
    def __init__(self, config_path: str):
        self._config_path = config_path
        self._config = load_config(config_path)
        self._proxy = ArduinoProxyClient(self._config.proxy_host, self._config.proxy_port)
        self._targets = self._build_targets(self._config)
        self._typer = ChatTyper(
            self._proxy,
            starts_in_korean_mode=self._config.starts_in_korean_mode,
            send_enter=self._config.send_enter,
        )
        self._repeater = AlternatingRepeater(self.send_message)

    def _build_targets(self, config: DualChatConfig) -> dict[str, WindowTarget]:
        return {
            role: WindowTarget(target.window_title_prefix)
            for role, target in config.targets.items()
        }

    @property
    def config(self) -> DualChatConfig:
        return self._config

    def close(self) -> None:
        self._repeater.stop()
        self._proxy.close()

    def reload(self) -> None:
        was_running = self._repeater.running
        self._repeater.stop()

        self._config = load_config(self._config_path)
        self._proxy.close()
        self._proxy = ArduinoProxyClient(self._config.proxy_host, self._config.proxy_port)
        self._targets = self._build_targets(self._config)
        self._typer = ChatTyper(
            self._proxy,
            starts_in_korean_mode=self._config.starts_in_korean_mode,
            send_enter=self._config.send_enter,
        )
        self._repeater = AlternatingRepeater(self.send_message)

        if was_running:
            print("[chat] repeat loop stopped during reload")

    def current_window(self, role: str) -> tuple[int, str]:
        return self._targets[role].resolve()

    def send_message(self, role: str, text: str) -> None:
        role = normalize_role(role)
        message = text.strip()
        if not message:
            raise RuntimeError("message is empty")

        hwnd, title = self._targets[role].resolve()
        focus_window(hwnd)
        if self._config.switch_delay_seconds > 0:
            time.sleep(self._config.switch_delay_seconds)
        self._typer.type_text(message)
        if self._config.send_enter and self._config.post_send_delay_seconds > 0:
            time.sleep(self._config.post_send_delay_seconds)
        print(f"[chat] sent to {role} '{title}': {message}")

        next_role = other_role(role)
        try:
            next_hwnd, next_title = self._targets[next_role].resolve()
            focus_window(next_hwnd)
            print(f"[chat] switched to {next_role} '{next_title}'")
        except Exception as exc:
            print(f"[chat] switch skipped ({next_role}): {exc}")

    def list_messages(self, role: str) -> list[str]:
        role = normalize_role(role)
        return list(self._config.targets[role].messages)

    def send_preset(self, role: str, index: int) -> None:
        messages = self.list_messages(role)
        if index < 1 or index > len(messages):
            raise RuntimeError(f"preset index out of range for {role}: {index}")
        self.send_message(role, messages[index - 1])

    def start_repeat(self, interval_seconds: float | None = None) -> None:
        interval = self._config.cycle_interval_seconds if interval_seconds is None else interval_seconds
        messages_by_role = {role: list(target.messages) for role, target in self._config.targets.items()}
        self._repeater.start(self._config.order, messages_by_role, interval)
        print(f"[chat] alternating loop started ({interval:.2f}s per send)")

    def stop_repeat(self) -> None:
        if not self._repeater.running:
            print("[chat] repeat loop is not running")
            return
        self._repeater.stop()
        print("[chat] repeat loop stopped")

    def print_status(self) -> None:
        print(f"[chat] proxy: {self._config.proxy_host}:{self._config.proxy_port}")
        print(f"[chat] send_enter: {self._config.send_enter}")
        print(f"[chat] cycle interval: {self._config.cycle_interval_seconds:.2f}s")
        print(f"[chat] switch delay: {self._config.switch_delay_seconds:.2f}s")
        print(f"[chat] post-send delay: {self._config.post_send_delay_seconds:.2f}s")
        print(f"[chat] order: {', '.join(self._config.order)}")
        print(f"[chat] repeat running: {self._repeater.running}")
        for role in VALID_ROLES:
            target = self._config.targets[role]
            try:
                hwnd, title = self.current_window(role)
                resolved = f"{title} ({hwnd})"
            except Exception as exc:
                resolved = f"unresolved: {exc}"
            print(
                f"[chat] {role}: prefix='{target.window_title_prefix}', "
                f"presets={len(target.messages)}, window={resolved}"
            )


def normalize_role(value: str) -> str:
    role = value.strip().casefold()
    if role not in VALID_ROLES:
        raise RuntimeError(f"role must be one of: {', '.join(VALID_ROLES)}")
    return role


def other_role(role: str) -> str:
    normalized = normalize_role(role)
    return "client" if normalized == "server" else "server"


def print_help() -> None:
    print("Commands")
    print("  help                      show this help")
    print("  status                    show target windows and proxy")
    print("  preset server             list server preset messages")
    print("  preset client             list client preset messages")
    print("  preset <role> <n>         send preset message n for role")
    print("  loop start [sec]          alternate server/client preset messages")
    print("  loop stop                 stop repeat loop")
    print("  reload                    reload dual_chat_macro.json")
    print("  send <role> <text>        send a single chat message")
    print("  server <text>             send text directly to server window")
    print("  client <text>             send text directly to client window")
    print("  quit                      exit")


def run_shell(chat_macro: DualChatMacro) -> int:
    print_help()
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not raw:
            continue

        try:
            parts = raw.split()

            if raw == "help":
                print_help()
            elif raw == "status":
                chat_macro.print_status()
            elif raw == "loop stop":
                chat_macro.stop_repeat()
            elif raw.startswith("loop start"):
                interval = float(parts[2]) if len(parts) >= 3 else None
                chat_macro.start_repeat(interval)
            elif raw == "reload":
                chat_macro.reload()
                print("[chat] config reloaded")
            elif raw in {"quit", "exit"}:
                return 0
            elif parts[0] == "preset":
                if len(parts) == 2:
                    role = normalize_role(parts[1])
                    messages = chat_macro.list_messages(role)
                    if not messages:
                        print(f"[chat] no preset messages configured for {role}")
                        continue
                    for idx, message in enumerate(messages, start=1):
                        print(f"  {role} {idx}. {message}")
                elif len(parts) == 3:
                    role = normalize_role(parts[1])
                    chat_macro.send_preset(role, int(parts[2]))
                else:
                    raise RuntimeError("usage: preset <role> [n]")
            elif parts[0] == "send":
                if len(parts) < 3:
                    raise RuntimeError("usage: send <role> <text>")
                role = normalize_role(parts[1])
                chat_macro.send_message(role, raw.split(maxsplit=2)[2])
            elif parts[0] in VALID_ROLES:
                role = normalize_role(parts[0])
                text = raw.split(maxsplit=1)[1] if len(parts) >= 2 else ""
                chat_macro.send_message(role, text)
            else:
                raise RuntimeError("unknown command")
        except Exception as exc:
            print(f"[chat] error: {exc}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Alternate chat-only macro for both server and client windows."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the JSON config file.",
    )
    parser.add_argument(
        "--role",
        choices=VALID_ROLES,
        help="Role to use with --send or --preset.",
    )
    parser.add_argument(
        "--send",
        help="Send one message and exit.",
    )
    parser.add_argument(
        "--preset",
        type=int,
        help="Send one preset message by 1-based index and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    chat_macro = DualChatMacro(args.config)
    try:
        if args.send or args.preset is not None:
            if not args.role:
                raise RuntimeError("--role is required with --send or --preset")
            if args.send:
                chat_macro.send_message(args.role, args.send)
                return 0
            if args.preset is not None:
                chat_macro.send_preset(args.role, args.preset)
                return 0
        return run_shell(chat_macro)
    finally:
        chat_macro.close()


if __name__ == "__main__":
    raise SystemExit(main())
