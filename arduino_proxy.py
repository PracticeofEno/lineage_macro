from __future__ import annotations

from macro_common.serial_proxy import run_serial_proxy

SERIAL_PORT = "COM3"
BAUD_RATE = 115200
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9998


def main() -> int:
    return run_serial_proxy(SERIAL_PORT, BAUD_RATE, PROXY_HOST, PROXY_PORT)


if __name__ == "__main__":
    raise SystemExit(main())
