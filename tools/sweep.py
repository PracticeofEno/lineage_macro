"""
정답 개수(프레임 0~9)에 맞춰 탐지 파라미터를 그리드 탐색한다.
오탐(과탐)을 미탐보다 FP_W 배 무겁게 처벌하여 '오탐율 낮은' 설정을 고른다.

단, 한 프레임도 0개로 죽어버리면(=사냥 매크로가 그 프레임에서 멈춤) 큰 벌점.
"""
import os
import sys
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cv2
import detect_pink_monster as D

GT = D.GROUND_TRUTH
imgs = {n: cv2.imread(os.path.join(D._SHOTS, f"{n}.png")) for n in GT}

FP_W = 2.0      # 오탐 가중치 (사용자 우선순위: 낮은 오탐율)
ZERO_PEN = 3.0  # 진짜 몬스터가 있는데 0개 탐지하면 프레임당 추가 벌점

best = None
grid = itertools.product(
    [3, 5, 7, 9],            # CLOSE_KSIZE
    [110, 130, 150, 170],    # CHAR_SMAX
    [0.34, 0.38, 0.42],      # MIN_FILL
    [120, 160, 200],         # MIN_AREA
)

for close_k, char_smax, min_fill, min_area in grid:
    D.CLOSE_KSIZE, D.CHAR_SMAX, D.MIN_FILL, D.MIN_AREA = close_k, char_smax, min_fill, min_area
    fp = fn = perfect = zero = 0
    for n, gt in GT.items():
        cnt = len(D.detect(imgs[n]))
        if cnt == 0 and gt > 0:
            zero += 1
        if cnt > gt:
            fp += cnt - gt
        elif cnt < gt:
            fn += gt - cnt
        else:
            perfect += 1
    cost = fp * FP_W + fn + zero * ZERO_PEN
    score = (cost, -perfect)
    if best is None or score < best[0]:
        best = (score, (close_k, char_smax, min_fill, min_area), fp, fn, zero, perfect)

(_, params, fp, fn, zero, perfect) = best
close_k, char_smax, min_fill, min_area = params
print(f"최적 (FP가중 {FP_W}, 0탐벌점 {ZERO_PEN}):")
print(f"  CLOSE_KSIZE={close_k}, CHAR_SMAX={char_smax}, MIN_FILL={min_fill}, MIN_AREA={min_area}")
print(f"  과탐(FP)={fp}, 미탐(FN)={fn}, 0개프레임={zero}, 정확일치={perfect}/{len(GT)}")

D.CLOSE_KSIZE, D.CHAR_SMAX, D.MIN_FILL, D.MIN_AREA = params
print("\n프레임별:")
for n, gt in GT.items():
    cnt = len(D.detect(imgs[n]))
    flag = "OK" if cnt == gt else f"{cnt-gt:+d}"
    print(f"  {n}: detect={cnt} truth={gt} {flag}")
