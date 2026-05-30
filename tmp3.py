"""
tmp_debug/ 의 det_N.png 와 mask_N.png 를 좌우로 합쳐 compare_N.png 로 저장.
"""
import cv2
import numpy as np
from pathlib import Path

debug_dir = Path("tmp_debug")
out_dir = Path("tmp_debug/compare")
out_dir.mkdir(exist_ok=True)

nums = sorted(
    {p.stem.split("_")[1].replace(".png", "") for p in debug_dir.glob("det_*.png")},
    key=lambda s: int(s.split(".")[0])
)

for n in nums:
    det_path = debug_dir / f"det_{n}.png"
    mask_path = debug_dir / f"mask_{n}.png"

    det = cv2.imread(str(det_path))
    mask = cv2.imread(str(mask_path))

    if det is None or mask is None:
        continue

    # 마스크를 det 와 같은 높이/너비로 맞춤 (혹시 다를 경우 대비)
    if mask.shape[:2] != det.shape[:2]:
        mask = cv2.resize(mask, (det.shape[1], det.shape[0]))

    # 마스크에 라벨 추가
    label_h = 28
    def add_label(img, text):
        labeled = np.zeros((img.shape[0] + label_h, img.shape[1], 3), dtype=np.uint8)
        labeled[label_h:] = img
        cv2.putText(labeled, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 1)
        return labeled

    det_l = add_label(det, f"det_{n}.png")
    mask_l = add_label(mask, f"mask_{n}.png")

    combined = np.hstack([det_l, mask_l])
    out_path = out_dir / f"compare_{n}.png"
    cv2.imwrite(str(out_path), combined)
    print(f"저장: {out_path}")

print(f"\n총 {len(nums)}개 → {out_dir}/")
