from __future__ import annotations

import argparse
import socket
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simple TCP connectivity test for one or more hosts."
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
        help="Socket connect timeout in seconds. Default: 5.0",
    )
    return parser.parse_args()


def test_host(host: str, port: int, timeout: float) -> tuple[bool, str]:
    started = time.perf_counter()
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return False, f"ERR {type(exc).__name__}: {exc} ({elapsed:.2f}s)"

    try:
        elapsed = time.perf_counter() - started
        local = conn.getsockname()
        remote = conn.getpeername()
        return True, f"OK local={local} remote={remote} ({elapsed:.2f}s)"
    finally:
        conn.close()


def main() -> int:
    args = parse_args()
    print(f"[tcp-test] port={args.port} timeout={args.timeout:.1f}s")
    for host in args.hosts:
        ok, result = test_host(host, args.port, args.timeout)
        status = "PASS" if ok else "FAIL"
        print(f"[tcp-test] {status} host={host} {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
