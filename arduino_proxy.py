"""
arduino_proxy.py - Arduino Serial Proxy
  - COM5 를 단독 점유
  - 127.0.0.1:9998 에서 명령을 수신해 Arduino 에 전달하고 응답을 반환
  - server.py / client.py 보다 먼저 실행해야 한다
"""

import socket
import threading
import serial
import sys

SERIAL_PORT = 'COM5'
BAUD_RATE   = 115200
PROXY_HOST  = '127.0.0.1'
PROXY_PORT  = 9998

# ── 시리얼 초기화 ─────────────────────────────────────────────────────────────
try:
    _ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"[proxy] Arduino 연결됨: {SERIAL_PORT} @ {BAUD_RATE}")
except serial.SerialException as e:
    print(f"[proxy] 시리얼 포트 열기 실패: {e}")
    sys.exit(1)

_ser_lock = threading.Lock()


def _handle_client(conn: socket.socket, addr: tuple):
    print(f"[proxy] 클라이언트 연결: {addr}")
    buf = b''
    try:
        while True:
            chunk = conn.recv(256)
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                cmd = line.decode().strip()
                if not cmd:
                    continue
                with _ser_lock:
                    _ser.write((cmd + '\n').encode())
                    resp = _ser.readline().decode().strip()
                conn.sendall((resp + '\n').encode())
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print(f"[proxy] 클라이언트 종료: {addr}")


# ── TCP 서버 ──────────────────────────────────────────────────────────────────
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((PROXY_HOST, PROXY_PORT))
srv.listen(10)
print(f"[proxy] 대기 중: {PROXY_HOST}:{PROXY_PORT}")
print("종료하려면 Ctrl+C")

try:
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
except KeyboardInterrupt:
    print("[proxy] 종료")
finally:
    srv.close()
    _ser.close()
