from PIL import Image
import numpy as np
import os
import json
from collections import defaultdict


def pixels_to_string(arr):
    mask = (arr[:,:,0] == 255) & (arr[:,:,1] == 255) & (arr[:,:,2] == 255)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)


BASE = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE, "data2")

def sort_key(f):
    stem = f.replace(".png", "")
    return (0, int(stem)) if stem.isascii() and stem.lstrip('-').isdigit() else (1, ord(stem[0]))

files = sorted(os.listdir(PROCESSED_DIR), key=sort_key)

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
