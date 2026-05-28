from __future__ import annotations

import socket
import threading

import serial

ACCEPT_TIMEOUT_SECONDS = 0.2
CLIENT_RECV_TIMEOUT_SECONDS = 0.2


def handle_serial_proxy_client(
    conn: socket.socket,
    addr: tuple,
    ser: serial.Serial,
    ser_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    print(f"[proxy] client connected: {addr}")
    buf = b""
    conn.settimeout(CLIENT_RECV_TIMEOUT_SECONDS)
    try:
        while not stop_event.is_set():
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                continue
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


def run_serial_proxy(serial_port: str, baud_rate: int, host: str, port: int) -> int:
    try:
        ser = serial.Serial(serial_port, baud_rate, timeout=1)
    except serial.SerialException as exc:
        print(f"[proxy] failed to open serial port {serial_port}: {exc}")
        return 1

    ser_lock = threading.Lock()
    stop_event = threading.Event()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(10)
        server.settimeout(ACCEPT_TIMEOUT_SECONDS)
    except OSError as exc:
        print(f"[proxy] failed to listen on {host}:{port}: {exc}")
        server.close()
        ser.close()
        return 1

    print(f"[proxy] serial={serial_port} baud={baud_rate}")
    print(f"[proxy] listening on {host}:{port}")

    try:
        while not stop_event.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            threading.Thread(
                target=handle_serial_proxy_client,
                args=(conn, addr, ser, ser_lock, stop_event),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print()
        print("[proxy] stopped")
    finally:
        stop_event.set()
        server.close()
        ser.close()
    return 0
