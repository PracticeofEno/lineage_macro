"""
hp_macro_server.py - HP/MP 감시 서버
  - HP/MP 화면 읽기 → 임계치 이하 시 클라이언트에 명령 전송
  - hold_f5 : 클라이언트가 F5 홀드 + 좌클릭 수행
  - press_f8: 클라이언트가 F8 입력 수행
"""

import argparse
from collections import Counter
import cv2
import json
import socket
import threading
import sys
import time
from pathlib import Path
from typing import Any

import tkinter as tk
import numpy as np
import win32api
import win32con
import win32gui
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro
from detect_pink_monster import detect as detect_pink_monsters


# ═══════════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════════

WINDOW_TITLE             = "server"
HP_PERCENT_THRESHOLD     = 70.0
MP_PERCENT_THRESHOLD     = 10.0
POLL_INTERVAL_SECONDS    = 0.2
F5_HOLD_SECONDS          = 1.0
F8_COOLDOWN_SECONDS      = 1800.0
F8_INTERVAL_SECONDS      = 1800.0
TRIGGER_COOLDOWN_SECONDS = 2.5
STATUS_INTERVAL_SECONDS  = 1.0
PING_INTERVAL_SECONDS    = 5.0

MONSTER_EXP_TIMEOUT   = 15.0   # EXP 변화 없으면 다음 몬스터 탐색까지 최대 대기 시간(초)
MONSTER_POLL_INTERVAL = 1.0    # EXP 폴링 간격(초)
EXP_CHANGE_DELAY      = 3.0    # EXP 변화 후 idle 전환까지 대기 시간(초)

BASE_GAME_X     = 32779   # 귀환 기준 게임 좌표 X
BASE_GAME_Y     = 33205   # 귀환 기준 게임 좌표 Y
SCREEN_CENTER_X = 645     # 플레이어 화면 중심 X (절대좌표)
SCREEN_CENTER_Y = 380     # 플레이어 화면 중심 Y (절대좌표, 십자선 기준)
TILE_PX_X       = 40      # 1타일 이동 시 화면 X 변화량
TILE_PX_Y       = 20      # 1타일 이동 시 화면 Y 변화량
WALK_TILES      = 2       # EXP 변화 없을 때 한 번에 이동할 타일 수
MOVE_IF_NO_EXP_SECONDS = 30.0  # 이 시간 동안 EXP 변화 없으면 기준좌표로 이동

HP_READ: dict[str, Any] = {
    "x": 976, "y": 71, "width": 80, "height": 21,
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
# HP/MP 읽기  (캡처·텍스트 인식 모두 macro.py 위임)
# ═══════════════════════════════════════════════════════════════════

def _read_stat_text(cfg: dict, img: Image.Image) -> str:
    x, y  = int(cfg["x"]), int(cfg["y"])
    w, h  = int(cfg["width"]), int(cfg["height"])
    color = tuple(int(v) for v in cfg["color_rgb"])
    best  = ""
    for dx in cfg.get("x_offsets", [0]):
        cropped = macro.crop(img, x + int(dx), y, w, h)
        for ty in cfg.get("text_y_offsets", [0]):
            for tx in cfg.get("text_x_offsets", [0]):
                text = macro.read_text(cropped, int(tx), int(ty), color)
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
    img     = macro.screenshot()
    cropped = macro.crop(img, x, y, w, h).convert("RGB")
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
# 오버레이 공유 상태 (thread-safe)
# ═══════════════════════════════════════════════════════════════════

_ov_lock  = threading.Lock()
_ov_state: dict[str, Any] = {
    "monsters":   [],    # list[dict] – 클라이언트 상대좌표
    "drag_start": None,  # (sx, sy) 절대좌표
    "drag_end":   None,  # (sx, sy) 절대좌표
    "last_click": None,  # (sx, sy) 절대좌표 (이동 클릭)
    "hp_state":   None,
    "mp_state":   None,
    "hunt_state": "idle",
    "location":   None,  # (gx, gy) 게임 좌표
}


def _ov_update(**kw: Any) -> None:
    with _ov_lock:
        _ov_state.update(kw)


# ═══════════════════════════════════════════════════════════════════
# 오버레이 UI (tkinter, 메인 스레드에서 실행)
# ═══════════════════════════════════════════════════════════════════

class ServerOverlay:
    _BOX_COLOR    = "#00FF00"
    _DOT_COLOR    = "red"
    _TEXT_COLOR   = "#FFFF00"
    _CENTER_COLOR = "#00CFFF"
    _CLICK_COLOR  = "#FF8800"
    _DRAG_COLOR   = "#FF44FF"
    _CENTER_SIZE  = 16

    def __init__(self, hwnd: int) -> None:
        self._hwnd    = hwnd
        self._running = True

        self._root = tk.Tk()
        self._root.title("hp_macro_overlay")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "black")
        self._root.configure(bg="black")

        self._canvas = tk.Canvas(self._root, bg="black", highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._root.update()
        self._make_clickthrough()

        self._root.bind("<Escape>", lambda _: self._stop())
        self._refresh()
        self._root.mainloop()

    def _make_clickthrough(self) -> None:
        """Keep the overlay visible without receiving mouse/keyboard input."""
        overlay_hwnd = win32gui.FindWindow(None, "hp_macro_overlay") or self._root.winfo_id()
        ex_style = win32gui.GetWindowLong(overlay_hwnd, win32con.GWL_EXSTYLE)
        ex_style |= (
            win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_NOACTIVATE
            | win32con.WS_EX_TOOLWINDOW
        )
        win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_EXSTYLE, ex_style)
        win32gui.SetWindowPos(
            overlay_hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED,
        )
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "black")

    def _stop(self) -> None:
        self._running = False
        self._root.quit()

    def _refresh(self) -> None:
        if not self._running:
            return
        try:
            x0, y0, x1, y1 = win32gui.GetWindowRect(self._hwnd)
        except Exception:
            self._stop()
            return

        w, h = x1 - x0, y1 - y0
        self._root.geometry(f"{w}x{h}+{x0}+{y0}")
        cv = self._canvas
        cv.delete("all")

        with _ov_lock:
            st = dict(_ov_state)
            monsters = list(st["monsters"])

        client_left, client_top = win32gui.ClientToScreen(self._hwnd, (0, 0))
        client_dx = client_left - x0
        client_dy = client_top - y0

        # ── 몬스터 박스 (클라이언트 상대좌표 → 오버레이 상대좌표) ──
        for m in monsters:
            bx, by, bw, bh = m["bbox"]
            mx, my = m["center"]
            bx += client_dx
            by += client_dy
            mx += client_dx
            my += client_dy
            cv.create_rectangle(bx, by, bx + bw, by + bh, outline=self._BOX_COLOR, width=2)
            cv.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill=self._DOT_COLOR, outline="")
            cv.create_text(bx + 2, by - 2, text=f"a={m['area']:.0f}",
                           fill=self._TEXT_COLOR, anchor="sw", font=("Consolas", 9))

        # ── 드래그 선 (절대 → 캔버스 상대) ─────────────────────
        ds, de = st["drag_start"], st["drag_end"]
        if ds and de:
            dsx, dsy = ds[0] - x0, ds[1] - y0
            dex, dey = de[0] - x0, de[1] - y0
            cv.create_line(dsx, dsy, dex, dey, fill=self._DRAG_COLOR, width=2, dash=(5, 3))
            cv.create_oval(dsx - 5, dsy - 5, dsx + 5, dsy + 5, outline=self._DRAG_COLOR, width=2)
            cv.create_oval(dex - 5, dey - 5, dex + 5, dey + 5, fill=self._DRAG_COLOR, outline="")
            cv.create_text(dex + 8, dey, text=f"({de[0]},{de[1]})",
                           fill=self._DRAG_COLOR, anchor="w", font=("Consolas", 9))

        # ── 이동 클릭 마커 (절대 → 캔버스 상대) ────────────────
        lc = st["last_click"]
        if lc:
            lcx, lcy = lc[0] - x0, lc[1] - y0
            r = 9
            cv.create_oval(lcx - r, lcy - r, lcx + r, lcy + r, outline=self._CLICK_COLOR, width=2)
            cv.create_line(lcx - r - 4, lcy, lcx + r + 4, lcy, fill=self._CLICK_COLOR, width=1)
            cv.create_line(lcx, lcy - r - 4, lcx, lcy + r + 4, fill=self._CLICK_COLOR, width=1)
            cv.create_text(lcx + r + 6, lcy, text=f"이동({lc[0]},{lc[1]})",
                           fill=self._CLICK_COLOR, anchor="w", font=("Consolas", 9))

        # ── 플레이어 중심 십자선 (절대 → 캔버스 상대) ───────────
        px = SCREEN_CENTER_X - x0
        py = SCREEN_CENTER_Y - y0
        s  = self._CENTER_SIZE
        cv.create_line(px - s, py, px + s, py, fill=self._CENTER_COLOR, width=2)
        cv.create_line(px, py - s, px, py + s, fill=self._CENTER_COLOR, width=2)
        cv.create_oval(px - 3, py - 3, px + 3, py + 3, outline=self._CENTER_COLOR, width=2)
        cv.create_text(px + s + 4, py, text=f"({SCREEN_CENTER_X},{SCREEN_CENTER_Y})",
                       fill=self._CENTER_COLOR, anchor="w", font=("Consolas", 9))

        # ── 상태 텍스트 (좌상단) ─────────────────────────────────
        hp, mp, loc = st["hp_state"], st["mp_state"], st["location"]
        hp_s  = f"HP {hp['current']}/{hp['maximum']} ({hp['percent']:.1f}%)" if hp else "HP --"
        mp_s  = f"MP {mp['current']}/{mp['maximum']} ({mp['percent']:.1f}%)" if mp else "MP --"
        loc_s = (f"위치({loc[0]},{loc[1]}) → 목표({BASE_GAME_X},{BASE_GAME_Y})"
                 if loc else f"위치 -- → 목표({BASE_GAME_X},{BASE_GAME_Y})")
        lines = [
            f"{hp_s}   {mp_s}",
            loc_s,
            f"hunt={st['hunt_state']}  몬스터={len(monsters)}  종료=콘솔 Ctrl+C",
        ]
        for i, line in enumerate(lines):
            cv.create_text(8, 8 + i * 17, text=line, fill=self._TEXT_COLOR,
                           anchor="nw", font=("Consolas", 10, "bold"))

        self._root.after(100, self._refresh)


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


def _press_f8_local() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F8)


# ═══════════════════════════════════════════════════════════════════
# 몬스터 탐지 및 공격
# ═══════════════════════════════════════════════════════════════════

DRAG_TARGET_X = 616
DRAG_TARGET_Y = 801
MONSTER_DRAG_CLIENT_MARGIN = 6
MONSTER_DRAG_CLIENT_TOP_MARGIN = 10


def _bgr_screenshot() -> np.ndarray:
    img_pil = macro.screenshot()
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


def _detect_pink_monsters_for_overlay(img_bgr: np.ndarray) -> list[dict[str, Any]]:
    monsters: list[dict[str, Any]] = []
    for d in detect_pink_monsters(img_bgr):
        x, y, w, h = int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])
        cx, cy = int(d["cx"]), int(d["cy"])
        monsters.append({
            "center": (cx, cy),
            "bbox": (x, y, w, h),
            "area": int(d["area"]),
        })
    return monsters


def _setcursor_drag(x1: int, y1: int, x2: int, y2: int, steps: int = 25, step_delay: float = 0.01) -> None:
    """SetCursorPos로 이동, Arduino LP/DR로 좌버튼 누름/뗌."""
    _ov_update(drag_start=(x1, y1), drag_end=(x2, y2))
    # macro.force_set_foreground_window(macro.lineage1_hwnd)
    # time.sleep(0.3)
    win32api.SetCursorPos((x1, y1))
    time.sleep(0.1)
    macro.arduino_mouse_left_down()
    time.sleep(0.1)
    for i in range(1, steps + 1):
        t = i / steps
        win32api.SetCursorPos((round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t)))
        time.sleep(0.001)
    macro.arduino_mouse_left_up()
    time.sleep(0.1)


def _client_to_screen_xy(x: int, y: int) -> tuple[int, int]:
    left, top = win32gui.ClientToScreen(macro.lineage1_hwnd, (0, 0))
    return left + x, top + y


def _client_screen_rect() -> tuple[int, int, int, int]:
    left, top = win32gui.ClientToScreen(macro.lineage1_hwnd, (0, 0))
    _client_left, _client_top, client_right, client_bottom = win32gui.GetClientRect(macro.lineage1_hwnd)
    return left, top, left + client_right, top + client_bottom


def _clamp_to_client_drag_area(x: int, y: int) -> tuple[int, int]:
    left, top, right, bottom = _client_screen_rect()
    min_x = left + MONSTER_DRAG_CLIENT_MARGIN
    max_x = right - MONSTER_DRAG_CLIENT_MARGIN
    min_y = top + MONSTER_DRAG_CLIENT_TOP_MARGIN
    max_y = bottom - MONSTER_DRAG_CLIENT_MARGIN
    return min(max(x, min_x), max_x), min(max(y, min_y), max_y)


def _move_toward_base() -> None:
    """read_location()으로 현재 좌표를 읽어 기준좌표 방향으로 WALK_TILES 타일 이동."""
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    time.sleep(0.1)
    loc = macro.read_location()
    if not loc or loc == (0, 0):
        print("[hp_macro_server] 좌표 읽기 실패, 이동 생략")
        return

    cur_x, cur_y = loc
    dx = BASE_GAME_X - cur_x
    dy = BASE_GAME_Y - cur_y

    if dx == 0 and dy == 0:
        return

    dist = (dx * dx + dy * dy) ** 0.5
    steps = min(WALK_TILES, max(1, round(dist)))

    step_dx = round(dx / dist * steps)
    step_dy = round(dy / dist * steps)

    # Δsx = 40*(Δgx+Δgy),  Δsy = 20*(Δgy-Δgx)
    screen_x = SCREEN_CENTER_X + (step_dx + step_dy) * TILE_PX_X
    screen_y = SCREEN_CENTER_Y + (step_dy - step_dx) * TILE_PX_Y

    print(
        f"[hp_macro_server] 기준좌표 이동:"
        f" 현재=({cur_x},{cur_y})"
        f" → 목표=({BASE_GAME_X},{BASE_GAME_Y})"
        f" dist={dist:.1f}"
        f" → 클릭=({screen_x},{screen_y})"
    )

    _ov_update(location=loc, last_click=(screen_x, screen_y))
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.5)
    macro.arduino_mouse_click_left()
    time.sleep(0.5)


def click_drag_monster(cx: int, cy: int) -> None:
    """몬스터 좌표(cx, cy)를 클릭 후 고정 좌표까지 드래그하고 버튼을 릴리스한다."""
    screen_x, screen_y = _client_to_screen_xy(cx, cy)
    safe_x, safe_y = _clamp_to_client_drag_area(screen_x, screen_y)
    if (safe_x, safe_y) != (screen_x, screen_y):
        print(
            f"[hp_macro_server] 드래그 시작점 보정:"
            f" client=({cx},{cy}) screen=({screen_x},{screen_y})"
            f" → safe=({safe_x},{safe_y})"
        )
    print(f"[hp_macro_server] 드래그: ({safe_x},{safe_y}) → ({DRAG_TARGET_X},{DRAG_TARGET_Y})")
    _setcursor_drag(safe_x, safe_y, DRAG_TARGET_X, DRAG_TARGET_Y)


def _detect_monster() -> tuple[int, int] | None:
    """스크린샷에서 가장 큰 몬스터를 탐지하고 중심 좌표를 반환한다. 없으면 None."""
    try:
        img_bgr = _bgr_screenshot()
        monsters = _detect_pink_monsters_for_overlay(img_bgr)
        _ov_update(monsters=monsters)
        if not monsters:
            return None
        biggest = max(monsters, key=lambda m: m['area'])
        return biggest['center']
    except Exception as e:
        print(f"[hp_macro_server] 몬스터 탐지 오류: {e}")
        _ov_update(monsters=[])
        return None


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
    last_trigger_time    = 0.0
    last_status_time     = 0.0
    last_f8_time         = 0.0
    last_periodic_f8     = 0.0
    last_exp_check       = 0.0

    # 30초 EXP 무변화 이동 추적
    last_exp_gain_time:  float      = time.time()
    last_global_exp:     str | None = None
    last_global_exp_poll: float     = 0.0

    # 몬스터 사냥 상태머신: 'idle' | 'hunting' | 'exp_changed'
    hunt_state:      str        = 'idle'
    hunt_prev_exp:   str | None = None
    hunt_start:      float      = 0.0
    hunt_exp_changed_at: float  = 0.0

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
        img      = macro.screenshot()
        hp_state = read_hp_state(img)
        mp_state = read_mp_state(img)
        now      = time.time()

        _ov_update(hp_state=hp_state, mp_state=mp_state, hunt_state=hunt_state)

        # ── 상태 출력 ──────────────────────────────────────────────
        if now - last_status_time >= STATUS_INTERVAL_SECONDS:
            print(f"[hp_macro_server] {_fmt_stat('HP', hp_state)}, {_fmt_stat('MP', mp_state)}"
                  f"  hunt={hunt_state}")
            last_status_time = now

        # ── 주기적 F8 (서버 로컬) ─────────────────────────────────
        if now - last_periodic_f8 >= F8_INTERVAL_SECONDS:
            print(f"[hp_macro_server] {F8_INTERVAL_SECONDS:.0f}초 주기 F8")
            _press_f8_local()
            last_periodic_f8 = time.time()

        # ── MP 체크 ────────────────────────────────────────────────
        if mp_state is not None:
            mp_low   = mp_state["percent"] < MP_PERCENT_THRESHOLD
            f8_ready = now - last_f8_time >= F8_COOLDOWN_SECONDS
            if mp_low and f8_ready:
                print(f"[hp_macro_server] MP {mp_state['percent']:.1f}% < {MP_PERCENT_THRESHOLD:.1f}% → press_f8")
                if _broadcast({"cmd": "press_f8"}, timeout=5.0):
                    last_f8_time = time.time()

        # ── HP 체크 (몬스터 사냥보다 우선) ────────────────────────
        if hp_state is not None:
            hp_low        = hp_state["maximum"] - hp_state["current"] > 30
            client_trigger = hp_state["maximum"] != hp_state["current"]
            trigger_ready = now - last_trigger_time >= TRIGGER_COOLDOWN_SECONDS

            if client_trigger and trigger_ready:
                t = threading.Thread(
                    target=_broadcast,
                    args=({"cmd": "hold_f5", "seconds": F5_HOLD_SECONDS}, F5_HOLD_SECONDS + 5.0),
                    daemon=True,
                )
                t.start()
                last_trigger_time = time.time()

            if hp_low and trigger_ready:
                print(f"[hp_macro_server] HP {hp_state['percent']:.1f}% < {HP_PERCENT_THRESHOLD:.1f}% → hold_f5 {F5_HOLD_SECONDS}s")
                _hold_f5(F5_HOLD_SECONDS)
                if client_trigger:
                    t.join()
                # HP 회복 직후 몬스터 재탐지
                hunt_state = 'idle'

        # ── 30초 EXP 무변화 → 기준좌표 이동 ──────────────────────────
        if now - last_global_exp_poll >= MONSTER_POLL_INTERVAL:
            last_global_exp_poll = now
            g_exp = macro.readExp()
            if last_global_exp is None:
                last_global_exp    = g_exp
                last_exp_gain_time = now
            elif g_exp != last_global_exp:
                last_global_exp    = g_exp
                last_exp_gain_time = now

        if now - last_exp_gain_time >= MOVE_IF_NO_EXP_SECONDS:
            print(f"[hp_macro_server] {MOVE_IF_NO_EXP_SECONDS:.0f}초 EXP 변화 없음, 기준좌표로 이동")
            _move_toward_base()
            last_exp_gain_time = time.time()

        # ── 몬스터 사냥 상태머신 ───────────────────────────────────
        if hunt_state == 'idle':
            pos = _detect_monster()
            if pos is not None:
                cx, cy = pos
                print(f"[hp_macro_server] 몬스터 탐지됨: center=({cx}, {cy})")
                click_drag_monster(cx, cy)
                hunt_prev_exp  = macro.readExp()
                hunt_start     = time.time()
                last_exp_check = time.time()
                hunt_state     = 'hunting'

        elif hunt_state == 'hunting':
            if now - last_exp_check >= MONSTER_POLL_INTERVAL:
                last_exp_check = now
                curr_exp = macro.readExp()
                elapsed  = now - hunt_start
                if curr_exp != hunt_prev_exp:
                    print(f"[hp_macro_server] EXP 변화: {hunt_prev_exp} → {curr_exp}, {EXP_CHANGE_DELAY:.0f}초 후 다음 몬스터 탐색")
                    hunt_exp_changed_at = time.time()
                    hunt_state = 'exp_changed'
                elif elapsed >= MONSTER_EXP_TIMEOUT:
                    print(f"[hp_macro_server] {MONSTER_EXP_TIMEOUT:.0f}초 EXP 변화 없음, {EXP_CHANGE_DELAY:.0f}초 후 다음 몬스터 탐색")
                    hunt_exp_changed_at = time.time()
                    hunt_state = 'exp_changed'

        elif hunt_state == 'exp_changed':
            if now - hunt_exp_changed_at >= EXP_CHANGE_DELAY:
                hunt_state = 'idle'

        time.sleep(POLL_INTERVAL_SECONDS)


# ═══════════════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════════════

def _input_loop() -> None:
    print("[hp_macro_server] 명령어: 1=클라이언트 시작, 2=클라이언트 정지")
    while _server_running:
        try:
            cmd = input("> ").strip()
        except EOFError:
            break
        if cmd == "1":
            print("[hp_macro_server] 클라이언트에 start 명령 전송")
            _broadcast({"cmd": "start"}, timeout=5.0)
        elif cmd == "2":
            print("[hp_macro_server] 클라이언트에 stop 명령 전송")
            _broadcast({"cmd": "stop"}, timeout=5.0)

def main() -> int:
    global _server_running
    macro.set_hwnd(_find_window(WINDOW_TITLE))

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"[hp_macro_server] 대기 중: {HOST}:{PORT}")
    threading.Thread(target=_accept_loop, args=(server_sock,), daemon=True).start()
    threading.Thread(target=_input_loop, daemon=True).start()

    try:
        threading.Thread(target=run, daemon=True).start()
        ServerOverlay(macro.lineage1_hwnd)
    except KeyboardInterrupt:
        print("\n[hp_macro_server] 종료")
    finally:
        _server_running = False

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
