from PIL import Image
import numpy as np
import os
import json
from collections import defaultdict


def pixels_to_string(arr):
    mask = (arr[:,:,0] == 255) & (arr[:,:,1] == 0) & (arr[:,:,2] == 0)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)


BASE = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE, "..", "processed_data")

files = sorted(os.listdir(PROCESSED_DIR), key=lambda f: int(f.replace(".png", "")))

string_map = defaultdict(list)

for fname in files:
    if not fname.endswith(".png"):
        continue
    arr = np.array(Image.open(os.path.join(PROCESSED_DIR, fname)).convert("RGB"))
    s = pixels_to_string(arr)
    string_map[s].append(fname)

duplicates = {k: v for k, v in string_map.items() if len(v) > 1}
print(f"전체 파일: {len(files)}개")
print(f"고유 문자열: {len(string_map)}개")
print(f"중복 그룹: {len(duplicates)}개")
if duplicates:
    print("\n=== 중복 목록 ===")
    for s, fnames in duplicates.items():
        print(f"  {fnames}")
else:
    print("중복 없음")
