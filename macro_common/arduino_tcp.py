from __future__ import annotations

import socket
import threading
from collections.abc import Callable


class ProxyConnectionError(RuntimeError):
    pass


ConnectErrorFactory = Callable[[OSError, str, int], str]


class ArduinoProxyClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float = 3.0,
        connect_error_factory: ConnectErrorFactory | None = None,
    ):
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._connect_error_factory = connect_error_factory
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
        try:
            conn = socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)
        except OSError as exc:
            if self._connect_error_factory is not None:
                message = self._connect_error_factory(exc, self.host, self.port)
                raise ProxyConnectionError(message) from exc
            raise
        conn.settimeout(self.timeout_seconds)
        self._conn = conn

    def probe(self) -> str:
        try:
            conn = socket.create_connection((self.host, self.port), timeout=1)
        except OSError as exc:
            return f"unreachable ({exc})"
        try:
            conn.close()
        except OSError:
            pass
        return "reachable"

    def command(self, cmd: str) -> str:
        with self._lock:
            if self._conn is None:
                self._connect_unlocked()

            try:
                return self._send_and_read_unlocked(cmd)
            except ProxyConnectionError:
                raise
            except OSError:
                self._close_unlocked()
                self._connect_unlocked()
                return self._send_and_read_unlocked(cmd)

    def _send_and_read_unlocked(self, cmd: str) -> str:
        if self._conn is None:
            raise OSError("proxy not connected")

        self._conn.sendall((cmd + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = self._conn.recv(256)
            if not chunk:
                raise OSError("proxy closed connection")
            buf += chunk
        return buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()

    def expect_ok(self, cmd: str) -> None:
        resp = self.command(cmd)
        if resp != "OK":
            raise RuntimeError(f"proxy command failed: {cmd} -> {resp}")

    def key_down(self, vk: int) -> None:
        self.expect_ok(f"KD,{vk}")

    def key_up(self, vk: int) -> None:
        self.expect_ok(f"KU,{vk}")

    def key_press(self, vk: int) -> None:
        self.expect_ok(f"KP,{vk}")

    def init_cursor(self) -> None:
        self.expect_ok("INIT")

    def move_mouse_abs(self, x: int, y: int) -> None:
        self.expect_ok(f"MM,{x},{y}")

    def left_down(self) -> None:
        self.expect_ok("LD")

    def left_up(self) -> None:
        self.expect_ok("LU")
