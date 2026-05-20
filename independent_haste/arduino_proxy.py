from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from macro_common.serial_proxy import run_serial_proxy

SERIAL_PORT = "COM11"
BAUD_RATE = 115200
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9998


def main() -> int:
    return run_serial_proxy(SERIAL_PORT, BAUD_RATE, PROXY_HOST, PROXY_PORT)


if __name__ == "__main__":
    raise SystemExit(main())
