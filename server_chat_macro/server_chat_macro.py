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

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "server_chat_macro.json")
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
class ChatConfig:
    window_title_prefix: str
    proxy_host: str
    proxy_port: int
    starts_in_korean_mode: bool
    send_enter: bool
    default_interval_seconds: float
    messages: list[str]


def load_config(path: str) -> ChatConfig:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    messages = []
    for value in raw.get("messages", []):
        text = str(value).strip()
        if text:
            messages.append(text)

    return ChatConfig(
        window_title_prefix=str(raw.get("window_title_prefix", "server")).strip() or "server",
        proxy_host=str(raw.get("proxy_host", "127.0.0.1")).strip() or "127.0.0.1",
        proxy_port=int(raw.get("proxy_port", 9998)),
        starts_in_korean_mode=bool(raw.get("starts_in_korean_mode", True)),
        send_enter=bool(raw.get("send_enter", True)),
        default_interval_seconds=float(raw.get("default_interval_seconds", 10.0)),
        messages=messages,
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


class ServerWindowTarget:
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


class MessageRepeater:
    def __init__(self, send_func):
        self._send_func = send_func
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, messages: list[str], interval_seconds: float) -> None:
        if self.running:
            raise RuntimeError("repeat loop is already running")
        if not messages:
            raise RuntimeError("no messages configured")
        if interval_seconds <= 0:
            raise RuntimeError("interval must be greater than 0")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(list(messages), interval_seconds),
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

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self, messages: list[str], interval_seconds: float) -> None:
        idx = 0
        while not self._stop_event.is_set():
            try:
                self._send_func(messages[idx])
            except Exception as exc:
                print(f"[chat] repeat error: {exc}")
            idx = (idx + 1) % len(messages)
            if self._stop_event.wait(interval_seconds):
                break


class ServerChatMacro:
    def __init__(self, config_path: str):
        self._config_path = config_path
        self._config = load_config(config_path)
        self._proxy = ArduinoProxyClient(self._config.proxy_host, self._config.proxy_port)
        self._window_target = ServerWindowTarget(self._config.window_title_prefix)
        self._typer = ChatTyper(
            self._proxy,
            starts_in_korean_mode=self._config.starts_in_korean_mode,
            send_enter=self._config.send_enter,
        )
        self._repeater = MessageRepeater(self.send_message)

    @property
    def config(self) -> ChatConfig:
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
        self._window_target = ServerWindowTarget(self._config.window_title_prefix)
        self._typer = ChatTyper(
            self._proxy,
            starts_in_korean_mode=self._config.starts_in_korean_mode,
            send_enter=self._config.send_enter,
        )
        self._repeater = MessageRepeater(self.send_message)

        if was_running:
            print("[chat] repeat loop stopped during reload")

    def current_window(self) -> tuple[int, str]:
        return self._window_target.resolve()

    def send_message(self, text: str) -> None:
        message = text.strip()
        if not message:
            raise RuntimeError("message is empty")

        hwnd, title = self._window_target.resolve()
        focus_window(hwnd)
        self._typer.type_text(message)
        print(f"[chat] sent to '{title}': {message}")

    def list_messages(self) -> list[str]:
        return list(self._config.messages)

    def send_preset(self, index: int) -> None:
        messages = self.list_messages()
        if index < 1 or index > len(messages):
            raise RuntimeError(f"preset index out of range: {index}")
        self.send_message(messages[index - 1])

    def start_repeat(self, interval_seconds: float | None = None) -> None:
        interval = (
            self._config.default_interval_seconds
            if interval_seconds is None
            else interval_seconds
        )
        self._repeater.start(self.list_messages(), interval)
        print(f"[chat] repeat loop started ({interval:.2f}s)")

    def stop_repeat(self) -> None:
        if not self._repeater.running:
            print("[chat] repeat loop is not running")
            return
        self._repeater.stop()
        print("[chat] repeat loop stopped")

    def print_status(self) -> None:
        hwnd, title = self.current_window()
        print(f"[chat] target window: {title} ({hwnd})")
        print(f"[chat] proxy: {self._config.proxy_host}:{self._config.proxy_port}")
        print(f"[chat] presets: {len(self._config.messages)}")
        print(f"[chat] repeat running: {self._repeater.running}")


def print_help() -> None:
    print("Commands")
    print("  help               show this help")
    print("  status             show target window and proxy")
    print("  preset             list configured preset messages")
    print("  preset <n>         send preset message n")
    print("  loop start [sec]   repeat all preset messages")
    print("  loop stop          stop repeat loop")
    print("  reload             reload server_chat_macro.json")
    print("  send <text>        send a single chat message")
    print("  quit               exit")
    print("  <text>             any other input is sent immediately")


def run_shell(chat_macro: ServerChatMacro) -> int:
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
            if raw == "help":
                print_help()
            elif raw == "status":
                chat_macro.print_status()
            elif raw == "preset":
                messages = chat_macro.list_messages()
                if not messages:
                    print("[chat] no preset messages configured")
                    continue
                for idx, message in enumerate(messages, start=1):
                    print(f"  {idx}. {message}")
            elif raw.startswith("preset "):
                index = int(raw.split(maxsplit=1)[1])
                chat_macro.send_preset(index)
            elif raw == "loop stop":
                chat_macro.stop_repeat()
            elif raw.startswith("loop start"):
                parts = raw.split()
                interval = float(parts[2]) if len(parts) >= 3 else None
                chat_macro.start_repeat(interval)
            elif raw == "reload":
                chat_macro.reload()
                print("[chat] config reloaded")
            elif raw in {"quit", "exit"}:
                return 0
            elif raw.startswith("send "):
                chat_macro.send_message(raw[5:])
            else:
                chat_macro.send_message(raw)
        except Exception as exc:
            print(f"[chat] error: {exc}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arduino chat-only macro for the 'server' window."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the JSON config file.",
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
    chat_macro = ServerChatMacro(args.config)
    try:
        if args.send:
            chat_macro.send_message(args.send)
            return 0
        if args.preset is not None:
            chat_macro.send_preset(args.preset)
            return 0
        return run_shell(chat_macro)
    finally:
        chat_macro.close()


if __name__ == "__main__":
    raise SystemExit(main())
