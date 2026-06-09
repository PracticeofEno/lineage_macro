"""
tmp4.py - 리니지 클래식 '셀로브'(거미형 몬스터) 탐지 알고리즘

================================================================================
[ 탐지 원리 ]
--------------------------------------------------------------------------------
셀로브의 몸체와 다리는 어두운 갈색이라 동굴 배경에 거의 묻혀버린다.
하지만 얼굴에는 배경에 거의 등장하지 않는 두 가지 채도 높은 색이 항상 존재한다.

  1) 노란/올리브색으로 빛나는 두 눈   예) RGB (68, 56, 14)  -> R≈G, B 매우 낮음
  2) 눈 바로 아래의 붉은 입(송곳니)    예) RGB (81, 12, 15)  -> R 크고 G,B 매우 낮음

따라서 "수평으로 가까이 붙은 노란 눈 한 쌍 + 그 바로 아래의 붉은 입" 이라는
기하학적 조합을 찾으면 셀로브의 얼굴(=중심)을 안정적으로 특정할 수 있다.
색 하나만 보면 바위/이름표/횃불 등에서 오탐이 나지만, 눈쌍+입 기하 조건을
함께 요구하면 오탐이 거의 사라진다. (실측 858장에서 검증)

[ 좌표계 ]
--------------------------------------------------------------------------------
반환 좌표는 입력 이미지(보통 1280x958 풀 스크린샷)의 절대 픽셀 좌표.
play_area 로 게임 화면만 보도록 UI(하단 채팅창/우측 패널)는 제외한다.
================================================================================
"""

from dataclasses import dataclass
import numpy as np
from PIL import Image
from scipy import ndimage


# ----------------------------------------------------------------------------
# 색상 마스크 임계값 (실측 스크린샷에서 추출)
# ----------------------------------------------------------------------------
def _red_mouth_mask(R, G, B):
    """붉은 입/송곳니: R이 크고 G,B는 매우 낮은 채도 높은 빨강."""
    return (
        (R >= 45) & (R <= 150)
        & (G < 42) & (B < 46)
        & (R - G >= 38) & (R - B >= 32)
    )


def _yellow_eye_mask(R, G, B):
    """노란/올리브색 눈: R,G 둘 다 중간, B는 매우 낮음 (R이 G보다 약간 큼)."""
    return (
        (R >= 45) & (R <= 150)
        & (G >= 32) & (G <= 110)
        & (B < 36)
        & (R - B >= 30) & (G - B >= 18)
        & (R - G >= 2) & (R - G <= 50)
    )


# ----------------------------------------------------------------------------
# 탐지 파라미터
# ----------------------------------------------------------------------------
EYE_BLOB_MIN = 2          # 눈 하나로 인정할 최소 픽셀 수
EYE_BLOB_MAX = 40         # 눈 하나의 최대 픽셀 수 (이보다 크면 횃불/광원 등)
EYE_BLOB_MAX_W = 12       # 눈 블롭 최대 폭
EYE_BLOB_MAX_H = 9        # 눈 블롭 최대 높이

PAIR_MAX_DY = 4           # 두 눈의 세로 차이 허용치 (수평 정렬)
PAIR_MIN_GAP = 4          # 두 눈 사이 최소 가로 간격
PAIR_MAX_GAP = 16         # 두 눈 사이 최대 가로 간격
PAIR_SIZE_RATIO = 3.0     # 두 눈의 크기 비율 허용치 (좌우 눈은 비슷한 크기)

MOUTH_DY_MIN = 1          # 눈 아래 입 탐색 시작 거리
MOUTH_DY_MAX = 22         # 눈 아래 입 탐색 끝 거리
MOUTH_DX = 11             # 눈 중점 기준 좌우 입 탐색 폭
MOUTH_MIN_PIXELS = 5      # 입으로 인정할 최소 붉은 픽셀 수

DEDUP_DIST = 30           # 이 거리 안의 중복 탐지는 하나로 병합


@dataclass
class SpiderDetection:
    """탐지된 셀로브 한 마리."""
    x: int          # 얼굴 중심 x (눈 중점, 절대 좌표)
    y: int          # 얼굴 중심 y (눈 중점, 절대 좌표)
    score: float    # 신뢰도 점수 (높을수록 확실)
    eye_pixels: int # 눈 픽셀 수
    red_pixels: int # 입 픽셀 수
    eye_gap: int    # 두 눈 사이 간격

    @property
    def click_point(self) -> tuple[int, int]:
        """공격 클릭 좌표 - 얼굴보다 약간 아래(몸통 중심)를 노린다."""
        return (self.x, self.y + 14)


def _extract_eye_blobs(yellow_mask):
    """노란 마스크에서 '눈' 후보 블롭들을 추출한다."""
    lbl, n = ndimage.label(yellow_mask)
    blobs = []  # (cx, cy, size, ymax)
    for i in range(1, n + 1):
        ys, xs = np.where(lbl == i)
        size = len(ys)
        if size < EYE_BLOB_MIN or size > EYE_BLOB_MAX:
            continue
        w = xs.max() - xs.min() + 1
        h = ys.max() - ys.min() + 1
        if w > EYE_BLOB_MAX_W or h > EYE_BLOB_MAX_H:
            continue
        blobs.append((xs.mean(), ys.mean(), size, ys.max()))
    return blobs


def detect_spiders(
    image: Image.Image,
    play_area: tuple[int, int, int, int] | None = (0, 60, 1150, 640),
) -> list[SpiderDetection]:
    """
    이미지에서 셀로브를 모두 탐지한다.

    Args:
        image: PIL 이미지 (게임 풀 스크린샷)
        play_area: (x0, y0, x1, y1) 게임 화면 영역. UI(채팅/패널) 제외용.
                   None 이면 이미지 전체를 검사.

    Returns:
        SpiderDetection 리스트 (신뢰도 score 내림차순 정렬)
    """
    arr_full = np.array(image.convert("RGB")).astype(int)

    if play_area is None:
        ox, oy = 0, 0
        arr = arr_full
    else:
        x0, y0, x1, y1 = play_area
        ox, oy = x0, y0
        arr = arr_full[y0:y1, x0:x1]

    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    red = _red_mouth_mask(R, G, B)
    yellow = _yellow_eye_mask(R, G, B)

    blobs = _extract_eye_blobs(yellow)

    candidates: list[SpiderDetection] = []
    for a in range(len(blobs)):
        x1b, y1b, s1, ymax1 = blobs[a]
        for b in range(a + 1, len(blobs)):
            x2b, y2b, s2, ymax2 = blobs[b]

            # --- 두 눈이 수평으로 가깝게 붙어 있는가? ---
            if abs(y1b - y2b) > PAIR_MAX_DY:
                continue
            gap = abs(x1b - x2b)
            if gap < PAIR_MIN_GAP or gap > PAIR_MAX_GAP:
                continue
            if max(s1, s2) > PAIR_SIZE_RATIO * min(s1, s2):
                continue

            mid_x = (x1b + x2b) / 2.0
            mid_y = (y1b + y2b) / 2.0
            eye_bottom = max(ymax1, ymax2)

            # --- 눈 바로 아래에 붉은 입이 있는가? ---
            rx0 = max(int(mid_x - MOUTH_DX), 0)
            rx1 = int(mid_x + MOUTH_DX + 1)
            ry0 = max(int(eye_bottom + MOUTH_DY_MIN), 0)
            ry1 = int(eye_bottom + MOUTH_DY_MAX)
            red_cnt = int(red[ry0:ry1, rx0:rx1].sum())
            if red_cnt < MOUTH_MIN_PIXELS:
                continue

            # --- 신뢰도 점수 ---
            score = red_cnt * 1.5 + (s1 + s2) - gap * 0.3 - abs(y1b - y2b) * 2.0
            candidates.append(SpiderDetection(
                x=int(mid_x + ox),
                y=int(mid_y + oy),
                score=float(score),
                eye_pixels=int(s1 + s2),
                red_pixels=red_cnt,
                eye_gap=int(gap),
            ))

    # 점수 높은 순 정렬 후, 가까운 중복 탐지 병합
    candidates.sort(key=lambda d: d.score, reverse=True)
    result: list[SpiderDetection] = []
    for c in candidates:
        if all((c.x - r.x) ** 2 + (c.y - r.y) ** 2 > DEDUP_DIST ** 2 for r in result):
            result.append(c)
    return result


def detect_nearest_spider(
    image: Image.Image,
    origin: tuple[int, int] | None = None,
    play_area: tuple[int, int, int, int] | None = (0, 60, 1150, 640),
) -> SpiderDetection | None:
    """
    origin(보통 플레이어 캐릭터 위치, 기본=화면 중앙)에서 가장 가까운 셀로브 반환.
    공격 대상 선택용. 탐지 실패 시 None.
    """
    spiders = detect_spiders(image, play_area)
    if not spiders:
        return None
    if origin is None:
        origin = (image.width // 2, image.height // 2)
    ox, oy = origin
    return min(spiders, key=lambda d: (d.x - ox) ** 2 + (d.y - oy) ** 2)


# ----------------------------------------------------------------------------
# 단독 실행: screenshots 폴더로 탐지 성능 검증 + 박스 그린 이미지 저장
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import glob
    import sys
    from PIL import ImageDraw

    base = os.path.dirname(os.path.abspath(__file__))
    files = sorted(glob.glob(os.path.join(base, "screenshots", "*.png")))
    if not files:
        print("screenshots 폴더에 png가 없습니다.")
        sys.exit(1)

    out_dir = os.path.join(base, "spider_detect_out")
    os.makedirs(out_dir, exist_ok=True)

    # 인자로 step 을 받으면 그 간격으로만 검사 (기본 30장 간격으로 샘플 저장)
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    total_frames = 0
    frames_with_spider = 0
    total_spiders = 0

    for idx, path in enumerate(files):
        img = Image.open(path).convert("RGB")
        spiders = detect_spiders(img)
        total_frames += 1
        if spiders:
            frames_with_spider += 1
            total_spiders += len(spiders)

        # 샘플 프레임만 박스 그려 저장
        if idx % step == 0:
            draw = ImageDraw.Draw(img)
            for s in spiders:
                draw.rectangle(
                    [s.x - 45, s.y - 25, s.x + 45, s.y + 60],
                    outline=(0, 255, 0), width=3,
                )
                cx, cy = s.click_point
                draw.line([cx - 6, cy, cx + 6, cy], fill=(0, 255, 255), width=2)
                draw.line([cx, cy - 6, cx, cy + 6], fill=(0, 255, 255), width=2)
            out = os.path.join(out_dir, f"det_{idx:04d}_{len(spiders)}.png")
            img.save(out)
            coords = [(s.x, s.y, round(s.score)) for s in spiders]
            print(f"[{idx:4d}] {os.path.basename(path)} -> {len(spiders)}마리 {coords}")

    print("-" * 60)
    print(f"전체 프레임: {total_frames}")
    print(f"셀로브 탐지된 프레임: {frames_with_spider} "
          f"({frames_with_spider / total_frames * 100:.1f}%)")
    print(f"탐지된 셀로브 총합: {total_spiders}")
    print(f"박스 시각화 이미지: {out_dir}  (step={step})")
