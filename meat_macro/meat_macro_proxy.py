from __future__ import annotations

import argparse
import socket
import threading

import serial


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serial-to-TCP proxy for the meat macro Arduino sketch."
    )
    parser.add_argument("--serial-port", default="COM11")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9998)
    return parser.parse_args()


def handle_client(conn: socket.socket, addr: tuple, ser: serial.Serial, ser_lock: threading.Lock) -> None:
    print(f"[proxy] client connected: {addr}")
    buf = b""
    try:
        while True:
            chunk = conn.recv(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.decode("utf-8", errors="replace").strip()
                if not cmd:
                    continue
                with ser_lock:
                    ser.write((cmd + "\n").encode("utf-8"))
                    resp = ser.readline().decode("utf-8", errors="replace").strip()
                conn.sendall((resp + "\n").encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print(f"[proxy] client disconnected: {addr}")


def main() -> int:
    args = parse_args()
    ser = serial.Serial(args.serial_port, args.baud_rate, timeout=1)
    ser_lock = threading.Lock()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(10)

    print(f"[proxy] serial={args.serial_port} baud={args.baud_rate}")
    print(f"[proxy] listening on {args.host}:{args.port}")

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr, ser, ser_lock),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print()
        print("[proxy] stopped")
    finally:
        server.close()
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
