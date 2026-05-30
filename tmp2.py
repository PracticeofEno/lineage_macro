"""
연분홍/보라빛 몬스터 탐지 모듈.
- detect_from_bgr()  : numpy BGR 배열을 받아 직접 탐지 (라이브 스크린샷용)
- detect_monsters()  : 파일 경로를 받아 탐지 (배치/디버그용, 하위 호환)
- main()             : 'server' 윈도우 스크린샷을 찍어 즉시 탐지 후 출력
"""
import sys
import cv2
import numpy as np
from pathlib import Path

# 게임 화면 영역 경계 (하단 UI·우측 UI 패널 제외)
GAME_AREA_Y_RATIO = 0.65
GAME_AREA_X_RATIO = 0.955

# ── 하위 호환 상수 (tmp4.py 등에서 import) ──────────────────────────
PURPLE_LOWER = np.array([120, 45, 80])
PURPLE_UPPER = np.array([155, 255, 255])
MIN_MONSTER_AREA = 500
NIGHT_DAY_THRESHOLD = 130

# ── CLAHE 설정 (항상 적용) ────────────────────────────────────────────
_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

# ── 밝기 4단계 탐지 파라미터 (CLAHE 전처리 후 적용) ──────────────────
# 색조 범위: H 118-175 (OpenCV 0-180 스케일, 보라~분홍마젠타)
# avg_V 는 CLAHE 적용 전 원본 게임 영역 기준
#
# S(채도)는 CLAHE 후에도 거의 변하지 않는다.
# 몬스터 글로우 S ≈ 120-255, 배경 노이즈 S < 50 → S_min=60 이상으로 분리 가능.
#
# (max_avg_v, hsv_lower, hsv_upper, min_area)
_TIERS: list[tuple] = [
    ( 90, np.array([120, 65, 45]), np.array([175, 255, 255]), 480),  # 매우 어두운 밤
    (130, np.array([118, 45, 35]), np.array([175, 255, 255]), 500),  # 어두운 밤
    (170, np.array([118, 28, 45]), np.array([173, 255, 255]), 480),  # 황혼/새벽
    (256, np.array([120, 38, 58]), np.array([170, 255, 255]), 400),  # 낮/밝은 설원
]


# ── 내부 유틸 ────────────────────────────────────────────────────────

def _is_night(game_area_bgr: np.ndarray) -> bool:
    avg_v = cv2.cvtColor(game_area_bgr, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    return avg_v < NIGHT_DAY_THRESHOLD


def _clahe_enhance(bgr: np.ndarray) -> np.ndarray:
    """LAB의 L채널에 CLAHE를 적용해 국소 대비를 향상시킨다."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = _CLAHE.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _get_tier_params(avg_v: float) -> tuple:
    for max_v, lower, upper, min_area in _TIERS:
        if avg_v < max_v:
            return lower, upper, min_area
    return _TIERS[-1][1:]


def _run_detection(game_area: np.ndarray) -> tuple[bool, list[dict], bool]:
    """
    게임 영역(BGR) 에서 몬스터를 탐지한다.
    Returns: (감지여부, 몬스터 목록, is_night)
    """
    avg_v = float(cv2.cvtColor(game_area, cv2.COLOR_BGR2HSV)[:, :, 2].mean())
    night = avg_v < NIGHT_DAY_THRESHOLD

    # 항상 CLAHE 적용 — 밝기와 무관하게 국소 대비를 정규화
    enhanced = _clahe_enhance(game_area)
    hsv      = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV)

    lower, upper, min_area = _get_tier_params(avg_v)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = np.ones((3, 3), np.uint8)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask   = cv2.dilate(mask, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    monsters = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area >= min_area:
            x, y, cw, ch = cv2.boundingRect(cnt)
            monsters.append({
                'center': (x + cw // 2, y + ch // 2),
                'bbox':   (x, y, cw, ch),
                'area':   area,
                'night':  night,
            })

    return len(monsters) > 0, monsters, night, mask


# ── 공개 API ─────────────────────────────────────────────────────────

def detect_from_bgr(img_bgr: np.ndarray, debug_name: str | None = None) -> tuple[bool, list[dict]]:
    """
    BGR numpy 배열(스크린샷)을 받아 몬스터를 탐지한다.

    Args:
        img_bgr    : 전체 게임 창 BGR 이미지
        debug_name : 지정 시 tmp_debug/ 에 시각화 저장 (예: 'live')
    Returns:
        (감지 여부, 몬스터 정보 리스트)
    """
    h, w = img_bgr.shape[:2]
    game_area = img_bgr[:int(h * GAME_AREA_Y_RATIO), :int(w * GAME_AREA_X_RATIO)]

    detected, monsters, night, mask = _run_detection(game_area)

    if debug_name:
        _save_debug(game_area, mask, monsters, night, debug_name)

    return detected, monsters


def detect_monsters(image_path: str | Path, debug: bool = False) -> tuple[bool, list[dict]]:
    """
    이미지 파일 경로를 받아 몬스터를 탐지한다. (배치/디버그용)
    """
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"이미지를 읽을 수 없음: {image_path}")

    debug_name = Path(image_path).name if debug else None
    detected, monsters = detect_from_bgr(img, debug_name=debug_name)
    return detected, monsters


def _save_debug(game_area: np.ndarray, mask: np.ndarray,
                monsters: list[dict], night: bool, name: str):
    debug_dir = Path("tmp_debug")
    debug_dir.mkdir(exist_ok=True)

    color = (0, 180, 255) if night else (0, 255, 0)
    vis   = game_area.copy()
    for m in monsters:
        x, y, cw, ch = m['bbox']
        cv2.rectangle(vis, (x, y), (x + cw, y + ch), color, 2)
        cv2.circle(vis, m['center'], 6, (0, 0, 255), -1)
        cv2.putText(vis, f"a={m['area']:.0f}", (x, max(0, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    label = "NIGHT" if night else "DAY"
    cv2.putText(vis, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    stem = Path(name).stem
    cv2.imwrite(str(debug_dir / f"det_{stem}.png"), vis)
    cv2.imwrite(str(debug_dir / f"mask_{stem}.png"), cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))


# ── main: 라이브 스크린샷으로 탐지 ──────────────────────────────────

def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from tmp import _find_server_hwnd, _screenshot

    print("'server' 윈도우 스크린샷 촬영...")
    try:
        hwnd    = _find_server_hwnd()
        img_pil = _screenshot(hwnd)
    except RuntimeError as e:
        print(f"[오류] {e}")
        sys.exit(1)

    # PIL RGB -> OpenCV BGR
    img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    h, w    = img_bgr.shape[:2]
    print(f"스크린샷 크기: {w} x {h}")

    detected, monsters = detect_from_bgr(img_bgr, debug_name="live.png")

    h_img, w_img = img_bgr.shape[:2]
    game_area    = img_bgr[:int(h_img * GAME_AREA_Y_RATIO), :int(w_img * GAME_AREA_X_RATIO)]
    avg_v        = float(cv2.cvtColor(game_area, cv2.COLOR_BGR2HSV)[:, :, 2].mean())
    night        = avg_v < NIGHT_DAY_THRESHOLD
    mode         = f"{'NIGHT' if night else 'DAY'} (avg_V={avg_v:.1f})"

    print(f"모드: {mode}  |  몬스터 감지: {'O' if detected else 'X'}  ({len(monsters)}개)")
    for m in monsters:
        cx, cy = m['center']
        print(f"  center=({cx},{cy})  area={m['area']:.0f}  bbox={m['bbox']}")

    print(f"\n디버그 이미지 -> tmp_debug/det_live.png")


if __name__ == "__main__":
    main()
