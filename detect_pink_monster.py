"""
연분홍빛(반투명) 몬스터 탐지 알고리즘
====================================

탐지 대상:
    눈밭 위에 떠 있는 "연분홍/라벤더색의 반투명한 구름/유령 형체".
    선명한 분홍 갑옷 캐릭터가 아니라, 배경이 비치는 옅은 분홍빛 덩어리다.

탐지 파이프라인 (오탐 최소화가 목표):
    [1] 색 신호  : G 채널이 R, B 보다 일정 값 이상 낮은 '분홍 틴트' 픽셀.
                  - 눈밭 배경    : R ≈ G ≈ B (무채색) → 색조 없음
                  - 반투명 몬스터: 연분홍~라벤더가 눈 위에 알파 블렌딩되어
                    R·B 가 G 보다 살짝 높다 (실측 BGR≈(240,202,205)).
                  채도가 매우 낮은 반투명 대상에도 잘 작동한다.
    [2] 형태 필터: 크기·종횡비·채움비율(fill)로 잡티 제거.
                  사용자가 지적한 바닥 잡티 'area≈167'(fill 0.29)를 배제.
    [3] ★캐릭터 배제: blob 내 '최대 채도(S_max)'로 판정.
                  실측(프레임 0~9 전체 blob)에서 두 부류가 채도로 깔끔히 갈린다:
                    · 반투명 라벤더 몬스터 : S_max 63~78 (저채도, 배경이 비침)
                    · 플레이어/NPC 분홍 갑옷: S_max 179~255 (선명한 고채도 코어)
                  그 사이(80~150)에는 실제 대상이 없어 임계 120 은 안전한 여유.
                  → blob 최대 채도가 임계 이상이면 캐릭터로 보고 제외.
                    (frame 0·7 등의 분홍 궁수 플레이어 오탐을 제거)

검증 결과 (사용자 제공 정답 개수 프레임 0~9):
    10프레임 중 3개 정확 일치, 나머지는 ±1~3. 사용자가 지적한 오탐
    (바닥 잡티 'area≈167', 분홍 캐릭터 박스)은 모두 사라졌다.
    남은 오차는 옅은 라벤더 구름이 서로 붙거나/갈라지는 경계 사례.

이 파일은 탐지 알고리즘·파라미터·검증 하니스를 함께 제공한다.
임계값은 모두 상단 상수로 노출 — tools/sweep.py 로 재튜닝할 수 있다.

────────────────────────────────────────────────────────────────────────
사용법
    # 1) 전체 프레임 탐지 + 주석 이미지/개수 출력 + 정답 개수와 비교
    python detect_pink_monster.py verify

    # 2) 한 프레임의 마스크 단계별 디버그 이미지 저장 (튜닝용)
    python detect_pink_monster.py debug 8

    # 3) 한 프레임에서 임의 좌표의 픽셀이 어떤 신호를 갖는지 출력 (색 샘플링)
    python detect_pink_monster.py sample 8 640 300
────────────────────────────────────────────────────────────────────────
"""

import os
import sys

import cv2
import numpy as np


# ══════════════════════════════════════════════════════════════════════
#  파라미터  (다른 모델이 calibrate/verify 결과를 보고 이 숫자만 조정하면 됨)
# ══════════════════════════════════════════════════════════════════════

# ── 탐색 영역 (1280×958 기준). UI 패널을 마스킹해 오탐 제거 ─────────────
ROI_TOP    = 0
ROI_BOTTOM = 632    # 하단 채팅/HP UI 패널 시작 y (이 아래는 무시)
ROI_LEFT   = 22     # 좌측 스탯 패널
ROI_RIGHT  = 1222   # 우측 스킬 패널

# ── ★ 핵심: 분홍 틴트 판별 (R·B 가 G 보다 높음) ────────────────────────
#   G_GAP_MIN   : (R-G) 와 (B-G) 가 각각 이 값 이상이면 분홍 후보.
#                 값이 작을수록 더 옅은(투명한) 몬스터까지 잡지만 오탐↑.
#   RB_BALANCE  : |R-B| 가 이 값 이하여야 함. 마젠타/라벤더는 R,B 가 비슷.
#                 (순수 빨강[R만 높음]·순수 파랑[B만 높음]을 배제해 오탐↓)
G_GAP_MIN  = 10
RB_BALANCE = 45

# ── 밝기/채도 보조 필터 (HSV) : 너무 어둡거나 무채색인 잡티 제거 ────────
V_MIN = 90      # 반투명이라도 눈 위라 밝다. 너무 어두운 그림자 제외
V_MAX = 255
S_MIN = 12      # 완전 무채색(눈) 배제. 단, 반투명이라 매우 낮게 둠

# ── 형태소 연산 : 흩어진 픽셀 정리 후 한 덩어리로 병합 ─────────────────
OPEN_KSIZE  = 2     # 점 노이즈 제거 (작게)
CLOSE_KSIZE = 3     # 한 몬스터의 끊긴 부위만 살짝 병합(크게 잡으면 인접
                    # 구름들이 한 덩어리로 붙어 개수가 줄어듦 → 3이 최적)

# ── blob 크기 필터 (병합 후 면적/형태) ────────────────────────────────
MIN_AREA   = 200     # 이보다 작으면 잡티
MAX_AREA   = 6000    # 이보다 크면 배경 덩어리/오탐
MIN_WIDTH  = 14
MIN_HEIGHT = 14

# ── 형태 필터 : 희박한 노이즈 제거 ────────────────────────────────────
#   MIN_FILL : 바운딩박스 안에서 마스크가 채우는 비율.
#   실측) 진짜 몬스터 = 0.46~0.77 로 꽉 참 / 사용자가 지적한 'area≈167'
#         바닥 잡티 = 0.29 로 희박. → 0.38 로 자르면 잡티만 제거됨.
MIN_FILL = 0.34

# ── ★ 캐릭터(선명한 분홍 갑옷) 배제 : blob 내 최대 채도 S_max 로 판정 ──
#   실측 데이터(프레임 0~9 전체 blob)에서 두 부류가 채도로 깔끔히 갈린다:
#     - 반투명 라벤더 몬스터 : S_max 63~78 (예외 없이 78 이하)
#     - 플레이어/NPC 분홍 갑옷: S_max 179~255 (선명한 고채도 코어 존재)
#   blob 내부 픽셀 최대 채도가 이 값 이상이면 '선명한 캐릭터'로 보고 제외.
#   110 은 몬스터(S_max≤78)와 캐릭터(S_max≥179) 사이의 안전한 경계.
CHAR_SMAX = 110


# ══════════════════════════════════════════════════════════════════════
#  탐지 파이프라인
# ══════════════════════════════════════════════════════════════════════

def _roi_mask(shape) -> np.ndarray:
    """UI 영역을 제외한 탐색 영역 마스크(uint8 0/255)."""
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    m[ROI_TOP:ROI_BOTTOM, ROI_LEFT:ROI_RIGHT] = 255
    return m


def pink_mask(img_bgr: np.ndarray) -> np.ndarray:
    """
    반투명 연분홍 픽셀 마스크를 만든다 (형태소 연산 전, 순수 색 신호).

    반환: uint8 마스크 (0 또는 255)
    """
    b, g, r = cv2.split(img_bgr.astype(np.int16))

    # ★ 핵심 신호: G 가 R, B 보다 충분히 낮고(분홍 틴트), R·B 는 균형.
    tint = (
        ((r - g) >= G_GAP_MIN) &
        ((b - g) >= G_GAP_MIN) &
        (np.abs(r - b) <= RB_BALANCE)
    )

    # 보조: 밝기/채도
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    bright = (v >= V_MIN) & (v <= V_MAX) & (s >= S_MIN)

    mask = (tint & bright).astype(np.uint8) * 255

    # UI 영역 제거
    mask = cv2.bitwise_and(mask, _roi_mask(img_bgr.shape))
    return mask


def _morph(mask: np.ndarray) -> np.ndarray:
    """노이즈 제거 후 덩어리 병합."""
    if OPEN_KSIZE > 0:
        k = np.ones((OPEN_KSIZE, OPEN_KSIZE), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    if CLOSE_KSIZE > 0:
        k = np.ones((CLOSE_KSIZE, CLOSE_KSIZE), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def detect(img_bgr: np.ndarray):
    """
    연분홍 몬스터 탐지.

    반환: list of dict
        {"cx","cy": 중심좌표, "x","y","w","h": 바운딩박스, "area": 면적}
    """
    mask = _morph(pink_mask(img_bgr))
    sat = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)[:, :, 1]   # 채도 채널

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

    out = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])

        # 1) 크기 필터
        if not (MIN_AREA <= area <= MAX_AREA):
            continue
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            continue

        # 2) 형태 필터 : 희박한 잡티(예: 바닥 'area≈167', fill 0.29) 제거
        fill = area / float(w * h)
        if fill < MIN_FILL:
            continue

        # 3) ★ 캐릭터 배제 : blob 내 최대 채도가 높으면 선명한 분홍 캐릭터
        blob = labels[y:y+h, x:x+w] == i
        s_vals = sat[y:y+h, x:x+w][blob]
        if s_vals.size == 0:
            continue
        if int(s_vals.max()) >= CHAR_SMAX:
            continue   # 선명한 분홍 갑옷 캐릭터로 판단 → 제외

        cx, cy = centroids[i]
        out.append({
            "cx": int(round(cx)), "cy": int(round(cy)),
            "x": x, "y": y, "w": w, "h": h, "area": area,
        })
    # 면적 큰 순
    out.sort(key=lambda d: -d["area"])
    return out


def draw(img_bgr, detections):
    """탐지 결과 주석."""
    vis = img_bgr.copy()
    for d in detections:
        cv2.rectangle(vis, (d["x"], d["y"]),
                      (d["x"] + d["w"], d["y"] + d["h"]), (0, 255, 0), 2)
        cv2.circle(vis, (d["cx"], d["cy"]), 4, (0, 255, 0), -1)
        cv2.putText(vis, str(d["area"]), (d["x"], d["y"] - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
    return vis


# ══════════════════════════════════════════════════════════════════════
#  검증 / 디버그 하니스  (다른 모델이 이걸로 튜닝)
# ══════════════════════════════════════════════════════════════════════

# 사용자가 알려준 정답 개수 (프레임 → 기대 몬스터 수). None=미상
GROUND_TRUTH = {
    0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 2, 6: 2, 7: 5, 8: 6, 9: 6,
}

_DIR = os.path.dirname(os.path.abspath(__file__))
_SHOTS = os.path.join(_DIR, "tmp_screenshots")


def _load(num):
    p = os.path.join(_SHOTS, f"{num}.png")
    img = cv2.imread(p)
    if img is None:
        raise FileNotFoundError(p)
    return img


def cmd_verify():
    """전체 프레임 탐지 → 주석 저장 + 정답 대비 개수 비교표."""
    out_dir = os.path.join(_SHOTS, "result")
    os.makedirs(out_dir, exist_ok=True)

    files = [f for f in os.listdir(_SHOTS)
             if f.endswith(".png") and f.split(".")[0].isdigit()]
    files.sort(key=lambda f: int(f.split(".")[0]))

    print(f"{'frame':>5} | {'detect':>6} | {'truth':>5} | diff")
    print("-" * 34)
    for f in files:
        num = int(f.split(".")[0])
        img = _load(num)
        dets = detect(img)
        cv2.imwrite(os.path.join(out_dir, f), draw(img, dets))

        gt = GROUND_TRUTH.get(num)
        if gt is None:
            print(f"{num:>5} | {len(dets):>6} | {'?':>5} |")
        else:
            diff = len(dets) - gt
            flag = "OK" if diff == 0 else f"{diff:+d}"
            print(f"{num:>5} | {len(dets):>6} | {gt:>5} | {flag}")
    print(f"\n주석 이미지: {out_dir}")


def cmd_debug(num):
    """한 프레임의 단계별 마스크 이미지 저장 (색신호 → 형태소 → 결과)."""
    img = _load(num)
    raw = pink_mask(img)
    morphed = _morph(raw)
    dets = detect(img)

    out_dir = os.path.join(_SHOTS, "debug")
    os.makedirs(out_dir, exist_ok=True)
    cv2.imwrite(os.path.join(out_dir, f"{num}_1_rawmask.png"), raw)
    cv2.imwrite(os.path.join(out_dir, f"{num}_2_morph.png"), morphed)
    cv2.imwrite(os.path.join(out_dir, f"{num}_3_result.png"), draw(img, dets))

    # 마스크를 원본 위에 빨강 오버레이
    overlay = img.copy()
    overlay[raw > 0] = (0, 0, 255)
    blended = cv2.addWeighted(img, 0.5, overlay, 0.5, 0)
    cv2.imwrite(os.path.join(out_dir, f"{num}_4_overlay.png"), blended)

    print(f"raw mask 픽셀수: {int((raw>0).sum())}")
    print(f"탐지 blob 수   : {len(dets)}")
    for d in dets:
        print(f"  center=({d['cx']},{d['cy']}) area={d['area']} box={d['w']}x{d['h']}")
    print(f"\n디버그 이미지: {out_dir}")


def cmd_sample(num, x, y, half=6):
    """(x,y) 주변 픽셀의 BGR/HSV/틴트 신호를 출력 (색 샘플링)."""
    img = _load(num)
    x, y = int(x), int(y)
    patch = img[y-half:y+half, x-half:x+half].reshape(-1, 3).astype(np.int16)
    b, g, r = patch[:, 0], patch[:, 1], patch[:, 2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[y-half:y+half, x-half:x+half].reshape(-1, 3)

    print(f"({x},{y}) 주변 {2*half}x{2*half} 평균")
    print(f"  BGR = ({b.mean():.0f},{g.mean():.0f},{r.mean():.0f})")
    print(f"  HSV = ({hsv[:,0].mean():.0f},{hsv[:,1].mean():.0f},{hsv[:,2].mean():.0f})")
    print(f"  R-G = {(r-g).mean():+.1f}   B-G = {(b-g).mean():+.1f}   "
          f"|R-B| = {np.abs(r-b).mean():.1f}")
    print(f"  → 현재 임계 G_GAP_MIN={G_GAP_MIN}, RB_BALANCE={RB_BALANCE}")


# ── 엔트리 포인트 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "verify":
        cmd_verify()
    elif cmd == "debug":
        cmd_debug(int(sys.argv[2]))
    elif cmd == "sample":
        cmd_sample(int(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        print(__doc__)
