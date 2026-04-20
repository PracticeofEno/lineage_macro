from __future__ import annotations

import argparse
import json
import socket
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test whether a TCP endpoint behaves like this project's pickup server."
    )
    parser.add_argument(
        "hosts",
        nargs="+",
        help="One or more hostnames or IP addresses to test.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9999,
        help="TCP port to test. Default: 9999",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket timeout in seconds. Default: 5.0",
    )
    parser.add_argument(
        "--idx",
        type=int,
        default=999,
        help="Client idx value to send in the register payload.",
    )
    return parser.parse_args()


def recv_line(conn: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0]


def test_host(host: str, port: int, timeout: float, idx: int) -> tuple[bool, str]:
    started = time.perf_counter()
    payload = {"cmd": "register", "idx": idx}
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
        conn.settimeout(timeout)
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        line = recv_line(conn)
        elapsed = time.perf_counter() - started
        if not line:
            return False, f"ERR no response after register ({elapsed:.2f}s)"
        text = line.decode("utf-8", errors="replace")
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            return False, f"ERR non-JSON response={text!r} ({elapsed:.2f}s)"
        if message.get("cmd") == "ping":
            return True, f"OK ping response={message!r} ({elapsed:.2f}s)"
        return False, f"ERR unexpected JSON response={message!r} ({elapsed:.2f}s)"
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return False, f"ERR {type(exc).__name__}: {exc} ({elapsed:.2f}s)"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    print(
        f"[pickup-handshake] port={args.port} timeout={args.timeout:.1f}s idx={args.idx}"
    )
    for host in args.hosts:
        ok, result = test_host(host, args.port, args.timeout, args.idx)
        status = "PASS" if ok else "FAIL"
        print(f"[pickup-handshake] {status} host={host} {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
