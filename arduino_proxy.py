"""
arduino_proxy.py - Arduino Serial Proxy
  - Scans COM0 through COM20 and uses the first serial port that opens.
  - Listens on 127.0.0.1:9998, forwards commands to Arduino, and returns replies.
  - Run this before server.py / client.py / hp_macro scripts.
"""

import socket
import sys
import threading

import serial

SERIAL_PORTS = [f"COM{i}" for i in range(0, 21)]
BAUD_RATE = 115200
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9998


def _open_serial() -> serial.Serial:
    """Scan COM0..COM20 and return the first serial port that opens."""
    errors: list[str] = []
    for port in SERIAL_PORTS:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1)
            print(f"[proxy] Arduino connected: {port} @ {BAUD_RATE}")
            return ser
        except serial.SerialException as e:
            errors.append(f"{port}: {e}")

    print("[proxy] Arduino serial port not found in COM0..COM20.")
    print("[proxy] Failed ports:")
    for error in errors:
        print(f"  - {error}")
    sys.exit(1)


_ser = _open_serial()
_ser_lock = threading.Lock()


def _handle_client(conn: socket.socket, addr: tuple):
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
                cmd = line.decode().strip()
                if not cmd:
                    continue
                with _ser_lock:
                    _ser.write((cmd + "\n").encode())
                    resp = _ser.readline().decode().strip()
                conn.sendall((resp + "\n").encode())
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print(f"[proxy] client closed: {addr}")


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((PROXY_HOST, PROXY_PORT))
srv.listen(10)
print(f"[proxy] listening: {PROXY_HOST}:{PROXY_PORT}")
print("Press Ctrl+C to stop")


def _accept_loop():
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()


threading.Thread(target=_accept_loop, daemon=True).start()

try:
    while True:
        threading.Event().wait(1)
except KeyboardInterrupt:
    print("[proxy] stopped")
finally:
    srv.close()
    _ser.close()
