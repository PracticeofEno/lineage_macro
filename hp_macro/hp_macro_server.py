"""
hp_macro_server.py - HP/MP 감시 서버
  - HP/MP 화면 읽기 → 임계치 이하 시 클라이언트에 명령 전송
  - hold_f5 : 클라이언트가 F5 홀드 + 좌클릭 수행
  - press_f8: 클라이언트가 F8 입력 수행
"""

import argparse
from collections import Counter
from ctypes import windll
import json
import socket
import threading
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import win32con
import win32gui
import win32ui
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro


# ═══════════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════════

WINDOW_TITLE             = "server"
HP_PERCENT_THRESHOLD     = 70.0
MP_PERCENT_THRESHOLD     = 10.0
POLL_INTERVAL_SECONDS    = 0.2
F5_HOLD_SECONDS          = 1.0
F8_COOLDOWN_SECONDS      = 600.0
TRIGGER_COOLDOWN_SECONDS = 2.5
STATUS_INTERVAL_SECONDS  = 1.0
PING_INTERVAL_SECONDS    = 5.0

HP_READ: dict[str, Any] = {
    "x": 980, "y": 100, "width": 80, "height": 21,
    "color_rgb":       (247, 201, 227),
    "x_offsets":       [0, 5, 10],
    "text_x_offsets":  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "text_y_offsets":  [0, 1, 2, 3, 4, 5],
}

MP_READ: dict[str, Any] = {
    "x": 976, "y": 96, "width": 100, "height": 21,
    "color_rgb":       (204, 227, 255),
    "x_offsets":       [0, 5, 10],
    "text_x_offsets":  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "text_y_offsets":  [0, 1, 2, 3, 4, 5],
}


# ═══════════════════════════════════════════════════════════════════
# 이미지 읽기 (내재화)
# ═══════════════════════════════════════════════════════════════════

# HP/MP 표시에 필요한 문자(숫자 0-9, /) 픽셀 패턴 → 문자 매핑
_CHAR_MAP: dict[str, str] = {
    "21321421548494104115556": "/",
    "08090100110120131819110111112113262728292102112122132142153103154541041555565758595105115125136667686961061176777879710711": "0",
    "262153631545464748494104114124134144155556575859510511512513514515615715": "1",
    "060130140151131141152521121221321421535315454104155556575859510515666768615767778713714715": "2",
    "060132521021535310315454104155556575859510511512513514515666768611612613767778711712713": "3",
    "01001111011128292102113114641155565758595105115125135145156667686961061161261376777879710711712713811911": "4",
    "050607080131516171825262728211215353831545484155558595105115125135145156561061161261375710711712713": "5",
    "06070809010011012013161718191102526272829210215353103154541041555565105115125135145156661161261376711712713": "6",
    "06253545410411412413414415555859510511512513657576": "7",
    "0607080110120131617181112526272829210211215353103154541041555585951051151251351451568611612613767778711712713": "8",
    "0801301401518115262728292102153103154541041555565758595105115125136667686961061176777879710711": "9",
}


def _lookup(coord_string: str) -> str | None:
    return _CHAR_MAP.get(coord_string)


def _image_to_coord_string(image: Image.Image, color: tuple) -> str:
    arr = np.array(image.convert("RGB"))
    r, g, b = color
    mask = (arr[:, :, 0] == r) & (arr[:, :, 1] == g) & (arr[:, :, 2] == b)
    ys, xs = np.where(mask)
    return "".join(f"{x}{y}" for x, y in sorted(zip(xs, ys)))


def _crop(image: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
    return image.crop((x, y, x + w, y + h))


def _read_text(image: Image.Image, x: int, y: int, color: tuple) -> str:
    result = []
    while x < image.width:
        for char_width in (10, 20):
            if x + char_width > image.width:
                continue
            s = _image_to_coord_string(_crop(image, x, y, char_width, 24), color)
            ch = _lookup(s)
            if ch is not None:
                result.append(ch)
                x += char_width
                break
        else:
            break
    return "".join(result)


def _screenshot(hwnd: int) -> Image.Image:
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap  = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bitmap)
    windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)

    bmpinfo = bitmap.GetInfo()
    bmpstr  = bitmap.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                           bmpstr, "raw", "BGRX", 0, 1)

    win32gui.DeleteObject(bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return img.crop((0, 0, img.width - 16, img.height - 41))


# ═══════════════════════════════════════════════════════════════════
# HP/MP 읽기
# ═══════════════════════════════════════════════════════════════════

def _read_stat_text(cfg: dict, img: Image.Image) -> str:
    x, y = int(cfg["x"]), int(cfg["y"])
    w, h  = int(cfg["width"]), int(cfg["height"])
    color = tuple(int(v) for v in cfg["color_rgb"])
    best  = ""
    for dx in cfg.get("x_offsets", [0]):
        cropped = _crop(img, x + int(dx), y, w, h)
        for ty in cfg.get("text_y_offsets", [0]):
            for tx in cfg.get("text_x_offsets", [0]):
                text = _read_text(cropped, int(tx), int(ty), color)
                if len(text) > len(best):
                    best = text
    return best


def _parse_stat(text: str) -> dict | None:
    if "/" not in text:
        return None
    cur_str, max_str = text.split("/", 1)
    cur_digits = "".join(c for c in cur_str if c.isdigit())
    max_digits = "".join(c for c in max_str if c.isdigit())
    if not cur_digits or not max_digits:
        return None
    current, maximum = int(cur_digits), int(max_digits)
    if maximum <= 0:
        return None
    return {"current": current, "maximum": maximum, "percent": current / maximum * 100.0}


def read_hp_state(img: Image.Image) -> dict | None:
    return _parse_stat(_read_stat_text(HP_READ, img))


def read_mp_state(img: Image.Image) -> dict | None:
    return _parse_stat(_read_stat_text(MP_READ, img))


def sample_hp_colors(limit: int) -> None:
    x, y = int(HP_READ["x"]), int(HP_READ["y"])
    w, h  = int(HP_READ["width"]), int(HP_READ["height"])
    img     = _screenshot(macro.lineage1_hwnd)
    cropped = _crop(img, x, y, w, h).convert("RGB")
    counts  = Counter(cropped.getdata())

    print(f"[hp_macro_server] sample  x={x} y={y} w={w} h={h}")

    print("[hp_macro_server] top colors:")
    for (r, g, b), n in counts.most_common(limit):
        print(f"  ({r},{g},{b}) #{r:02X}{g:02X}{b:02X}  count={n}")

    vivid = sorted(
        [(c, n) for c, n in counts.items() if max(c) >= 120 and max(c) - min(c) >= 25],
        key=lambda t: t[1], reverse=True,
    )
    print("[hp_macro_server] vivid candidates:")
    for (r, g, b), n in vivid[:limit]:
        print(f"  ({r},{g},{b}) #{r:02X}{g:02X}{b:02X}  count={n}")


# ═══════════════════════════════════════════════════════════════════
# TCP 서버
# ═══════════════════════════════════════════════════════════════════

HOST = '0.0.0.0'
PORT = 9997

_clients: list[dict]               = []
_clients_lock                      = threading.Lock()
_recv_buffers: dict[socket.socket, bytes] = {}
_request_id      = 0
_request_id_lock = threading.Lock()
_server_running  = True


def _send_json(conn: socket.socket, obj: dict) -> bool:
    try:
        conn.sendall((json.dumps(obj) + '\n').encode())
        return True
    except OSError:
        return False


def _recv_json(conn: socket.socket) -> dict | None:
    buf = _recv_buffers.pop(conn, b'')
    try:
        while b'\n' not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        line, rest = buf.split(b'\n', 1)
        if rest:
            _recv_buffers[conn] = rest
        return json.loads(line.decode())
    except (OSError, json.JSONDecodeError):
        return None


def _next_request_id() -> int:
    global _request_id
    with _request_id_lock:
        _request_id += 1
        return _request_id


def _recv_ack(conn: socket.socket, req_id: int, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            conn.settimeout(remaining)
            resp = _recv_json(conn)
            if resp is None:
                return None
            if resp.get("req_id") == req_id:
                return resp
            print(f"[hp_macro_server] req_id 불일치 응답 무시: {resp}")
    finally:
        conn.settimeout(None)


def _remove_client(client: dict) -> None:
    with _clients_lock:
        _clients[:] = [c for c in _clients if c is not client]
    try:
        client["conn"].close()
    except OSError:
        pass
    _recv_buffers.pop(client.get("conn"), None)
    print(f"[hp_macro_server] 클라이언트 제거됨: {client['addr']}")


def _send_command(client: dict, payload: dict, timeout: float) -> bool:
    conn, addr = client["conn"], client["addr"]
    with client["lock"]:
        req_id = _next_request_id()
        payload = {**payload, "req_id": req_id}
        if not _send_json(conn, payload):
            _remove_client(client)
            return False
        ack = _recv_ack(conn, req_id, timeout=timeout)
        if ack is None:
            print(f"[hp_macro_server] ack 없음: {addr}")
            _remove_client(client)
            return False
    return True


def _broadcast(payload: dict, timeout: float) -> bool:
    """모든 클라이언트에 명령을 병렬 전송. 한 곳 이상 성공 시 True."""
    with _clients_lock:
        clients = list(_clients)
    if not clients:
        print("[hp_macro_server] 연결된 클라이언트 없음")
        return False

    success = [False] * len(clients)

    def send_to(i: int, client: dict) -> None:
        success[i] = _send_command(client, dict(payload), timeout)

    threads = [
        threading.Thread(target=send_to, args=(i, c), daemon=True)
        for i, c in enumerate(clients)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return any(success)


def _handle_client(conn: socket.socket, addr: tuple) -> None:
    client: dict = {"conn": conn, "addr": addr, "lock": threading.Lock()}
    with _clients_lock:
        _clients.append(client)
    print(f"[hp_macro_server] 클라이언트 연결됨: {addr}")

    try:
        while _server_running:
            with client["lock"]:
                req_id = _next_request_id()
                if not _send_json(conn, {"cmd": "ping", "req_id": req_id}):
                    break
                if _recv_ack(conn, req_id, timeout=10.0) is None:
                    break
            time.sleep(PING_INTERVAL_SECONDS)
    finally:
        _remove_client(client)


def _accept_loop(server_sock: socket.socket) -> None:
    while _server_running:
        try:
            conn, addr = server_sock.accept()
            threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
        except OSError:
            break


# ═══════════════════════════════════════════════════════════════════
# 로컬 액션
# ═══════════════════════════════════════════════════════════════════

def _hold_f5(seconds: float) -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_down(win32con.VK_F5)
    try:
        time.sleep(seconds)
    finally:
        macro.arduino_key_up(win32con.VK_F5)


# ═══════════════════════════════════════════════════════════════════
# 윈도우 찾기
# ═══════════════════════════════════════════════════════════════════

def _find_window(title_prefix: str) -> int:
    windows: list[tuple[str, int]] = []

    def callback(hwnd: int, _) -> None:
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t:
                windows.append((t, hwnd))

    win32gui.EnumWindows(callback, None)

    for title, hwnd in windows:
        if title.startswith(title_prefix):
            return hwnd

    raise RuntimeError(f"window not found: {title_prefix!r}")


# ═══════════════════════════════════════════════════════════════════
# 메인 루프
# ═══════════════════════════════════════════════════════════════════

def _fmt_stat(name: str, state: dict | None) -> str:
    if state is None:
        return f"{name} read failed"
    return f"{name}={state['current']}/{state['maximum']} ({state['percent']:.1f}%)"


def run() -> None:
    last_trigger_time = 0.0
    last_status_time  = 0.0
    last_f8_time      = 0.0

    print(
        f"[hp_macro_server] start"
        f"  hp={HP_PERCENT_THRESHOLD:.1f}%"
        f"  mp={MP_PERCENT_THRESHOLD:.1f}%"
        f"  hold={F5_HOLD_SECONDS}s"
        f"  poll={POLL_INTERVAL_SECONDS}s"
        f"  cooldown={TRIGGER_COOLDOWN_SECONDS}s"
        f"  f8_cooldown={F8_COOLDOWN_SECONDS}s"
    )

    while True:
        img      = _screenshot(macro.lineage1_hwnd)
        hp_state = read_hp_state(img)
        mp_state = read_mp_state(img)
        now      = time.time()

        if now - last_status_time >= STATUS_INTERVAL_SECONDS:
            print(f"[hp_macro_server] {_fmt_stat('HP', hp_state)}, {_fmt_stat('MP', mp_state)}")
            last_status_time = now

        if mp_state is not None:
            mp_low     = mp_state["percent"] < MP_PERCENT_THRESHOLD
            f8_ready   = now - last_f8_time >= F8_COOLDOWN_SECONDS
            if mp_low and f8_ready:
                print(f"[hp_macro_server] MP {mp_state['percent']:.1f}% < {MP_PERCENT_THRESHOLD:.1f}% → press_f8")
                if _broadcast({"cmd": "press_f8"}, timeout=5.0):
                    last_f8_time = time.time()

        if hp_state is not None:
            hp_low        = hp_state["percent"] < HP_PERCENT_THRESHOLD
            trigger_ready = now - last_trigger_time >= TRIGGER_COOLDOWN_SECONDS
            if hp_low and trigger_ready:
                print(f"[hp_macro_server] HP {hp_state['percent']:.1f}% < {HP_PERCENT_THRESHOLD:.1f}% → hold_f5 {F5_HOLD_SECONDS}s")
                t = threading.Thread(
                    target=_broadcast,
                    args=({"cmd": "hold_f5", "seconds": F5_HOLD_SECONDS}, F5_HOLD_SECONDS + 5.0),
                    daemon=True,
                )
                t.start()
                _hold_f5(F5_HOLD_SECONDS)
                t.join()
                last_trigger_time = time.time()

        time.sleep(POLL_INTERVAL_SECONDS)


# ═══════════════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HP/MP 감시 서버 — 임계치 이하 시 클라이언트에 명령 전송")
    parser.add_argument("--once",         action="store_true", help="HP/MP 1회 읽고 종료")
    parser.add_argument("--sample-colors", action="store_true", help="HP 영역 색상 후보 출력 후 종료")
    parser.add_argument("--sample-limit", type=int, default=20, help="--sample-colors 출력 개수")
    return parser.parse_args()


def main() -> int:
    global _server_running

    args = parse_args()

    macro.set_hwnd(_find_window(WINDOW_TITLE))

    if args.sample_colors:
        sample_hp_colors(args.sample_limit)
        return 0

    if args.once:
        img = _screenshot(macro.lineage1_hwnd)
        print(f"[hp_macro_server] {_fmt_stat('HP', read_hp_state(img))}, {_fmt_stat('MP', read_mp_state(img))}")
        return 0

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[hp_macro_server] 대기 중: {HOST}:{PORT}")
    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()

    try:
        run()
    except KeyboardInterrupt:
        print("\n[hp_macro_server] 종료")
    finally:
        _server_running = False

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
