from PIL import Image
import numpy as np
import os
import json


def all_chars():
    for c in range(ord('a'), ord('z') + 1):
        yield chr(c)
    for c in range(ord('A'), ord('Z') + 1):
        yield chr(c)
    for c in range(0xAC00, 0xD7A4):
        yield chr(c)


def pixels_to_string(arr):
    mask = (arr[:,:,0] == 255) & (arr[:,:,1] == 0) & (arr[:,:,2] == 0)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)


BASE = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE, "..", "processed_data")
OUTPUT_JSON   = os.path.join(BASE, "..", "converted_data.json")

chars = list(all_chars())
files = sorted(os.listdir(PROCESSED_DIR), key=lambda f: int(f.replace(".png", "")))

lookup = {}
for fname in files:
    if not fname.endswith(".png"):
        continue
    idx = int(fname.replace(".png", ""))
    arr = np.array(Image.open(os.path.join(PROCESSED_DIR, fname)).convert("RGB"))
    s = pixels_to_string(arr)
    lookup[s] = chars[idx]

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(lookup, f, ensure_ascii=False, indent=2)

print(f"저장 완료: {OUTPUT_JSON} ({len(lookup)}개)")
