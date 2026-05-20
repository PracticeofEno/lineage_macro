from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from macro_common.serial_proxy import run_serial_proxy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serial-to-TCP proxy for the meat macro Arduino sketch."
    )
    parser.add_argument("--serial-port", default="COM11")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9998)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_serial_proxy(args.serial_port, args.baud_rate, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
