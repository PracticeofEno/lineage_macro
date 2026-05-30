"""
"server" 윈도우 위에 몬스터 탐지 결과를 실시간 투명 오버레이로 표시.
ESC 키로 종료.
"""
import sys
import time
import threading
import tkinter as tk
import numpy as np
import cv2
import win32gui
from PIL import ImageGrab
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tmp2 import GAME_AREA_Y_RATIO, GAME_AREA_X_RATIO, PURPLE_LOWER, PURPLE_UPPER, MIN_MONSTER_AREA

UPDATE_INTERVAL_MS = 150  # 오버레이 갱신 주기 (ms)
DETECT_INTERVAL    = 0.15  # 탐지 스레드 주기 (초)

BOX_COLOR    = "#00FF00"  # 바운딩 박스
DOT_COLOR    = "red"      # 중심점
TEXT_COLOR   = "#FFFF00"  # 면적 라벨
CENTER_COLOR = "#00CFFF"  # 화면 중앙 십자선
CENTER_SIZE  = 16         # 십자선 반지름 (px)


def find_game_window(keyword: str = "server") -> int | None:
    found = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if keyword.lower() in win32gui.GetWindowText(hwnd).lower():
                found.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def list_visible_windows():
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t:
                print(f"  [{hwnd:8d}] {t}")
    win32gui.EnumWindows(cb, None)


def detect_from_bgr(img_bgr: np.ndarray) -> list[dict]:
    h, w = img_bgr.shape[:2]
    game = img_bgr[:int(h * GAME_AREA_Y_RATIO), :int(w * GAME_AREA_X_RATIO)]

    hsv  = cv2.cvtColor(game, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, PURPLE_LOWER, PURPLE_UPPER)

    k    = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.dilate(mask, k, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    monsters = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area >= MIN_MONSTER_AREA:
            x, y, cw, ch = cv2.boundingRect(cnt)
            monsters.append({
                'center': (x + cw // 2, y + ch // 2),
                'bbox':   (x, y, cw, ch),
                'area':   area,
            })
    return monsters


class MonsterOverlay:
    def __init__(self, hwnd: int):
        self.hwnd    = hwnd
        self.running = True
        self.monsters: list[dict] = []
        self.lock = threading.Lock()

        # ── tkinter 투명 오버레이 윈도우 ──────────────────────────
        self.root = tk.Tk()
        self.root.title("monster_overlay")
        self.root.overrideredirect(True)          # 타이틀바/테두리 제거
        self.root.attributes("-topmost", True)    # 항상 최상위
        self.root.attributes("-transparentcolor", "black")  # 검정 → 투명
        self.root.configure(bg="black")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.root.bind("<Escape>", lambda _: self.stop())

        # ── 탐지 백그라운드 스레드 ────────────────────────────────
        threading.Thread(target=self._detect_loop, daemon=True).start()

        # ── 오버레이 갱신 루프 시작 ───────────────────────────────
        self._refresh()
        self.root.mainloop()

    def stop(self):
        self.running = False
        self.root.quit()

    # ── 백그라운드: 캡처 & 탐지 ──────────────────────────────────
    def _detect_loop(self):
        while self.running:
            try:
                rect = win32gui.GetWindowRect(self.hwnd)
                x0, y0, x1, y1 = rect
                img_pil = ImageGrab.grab(bbox=(x0, y0, x1, y1))
                img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
                result  = detect_from_bgr(img_bgr)
                with self.lock:
                    self.monsters = result
            except Exception as e:
                print(f"[탐지 오류] {e}")
            time.sleep(DETECT_INTERVAL)

    # ── 메인 스레드: 오버레이 그리기 ─────────────────────────────
    def _refresh(self):
        if not self.running:
            return

        # 게임 윈도우 위치/크기에 오버레이 맞추기
        try:
            x0, y0, x1, y1 = win32gui.GetWindowRect(self.hwnd)
        except Exception:
            self.stop()
            return

        w, h = x1 - x0, y1 - y0
        self.root.geometry(f"{w}x{h}+{x0}+{y0}")

        self.canvas.delete("all")

        with self.lock:
            monsters = list(self.monsters)

        for m in monsters:
            bx, by, bw, bh = m['bbox']
            cx, cy = m['center']

            self.canvas.create_rectangle(
                bx, by, bx + bw, by + bh,
                outline=BOX_COLOR, width=2
            )
            self.canvas.create_oval(
                cx - 4, cy - 4, cx + 4, cy + 4,
                fill=DOT_COLOR, outline=""
            )
            self.canvas.create_text(
                bx + 2, by - 2,
                text=f"a={m['area']:.0f}",
                fill=TEXT_COLOR, anchor="sw",
                font=("Consolas", 9)
            )

        # ── 화면 중앙 십자선 ─────────────────────────────────────
        cx, cy = w // 2, h // 2
        cy = cy - 120
        s = CENTER_SIZE
        self.canvas.create_line(cx - s, cy, cx + s, cy, fill=CENTER_COLOR, width=2)
        self.canvas.create_line(cx, cy - s, cx, cy + s, fill=CENTER_COLOR, width=2)
        self.canvas.create_oval(
            cx - 3, cy - 3, cx + 3, cy + 3,
            outline=CENTER_COLOR, width=2
        )
        self.canvas.create_text(
            cx + s + 4, cy,
            text=f"({cx},{cy})",
            fill=CENTER_COLOR, anchor="w",
            font=("Consolas", 9)
        )

        self.canvas.create_text(
            8, 8,
            text=f"monsters: {len(monsters)}   [ESC] 종료",
            fill=TEXT_COLOR, anchor="nw",
            font=("Consolas", 11, "bold")
        )

        self.root.after(UPDATE_INTERVAL_MS, self._refresh)


def main():
    hwnd = find_game_window("server")
    if hwnd is None:
        print("'server' 윈도우를 찾을 수 없습니다. 현재 보이는 윈도우 목록:")
        list_visible_windows()
        return

    title = win32gui.GetWindowText(hwnd)
    print(f"게임 윈도우 발견: [{hwnd}] \"{title}\"")
    print("오버레이 시작... ESC로 종료")
    MonsterOverlay(hwnd)


if __name__ == "__main__":
    main()
