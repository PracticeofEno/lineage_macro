from PIL import Image
import numpy as np
import os
from collections import defaultdict

def pixels_to_string(arr):
    """빨간 픽셀(255,0,0)의 x,y 좌표를 이어붙인 문자열 반환. 예: x=13,y=15 → '1315'"""
    mask = (arr[:,:,0] == 255) & (arr[:,:,1] == 0) & (arr[:,:,2] == 0)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)


files = sorted(os.listdir("processed_data"), key=lambda f: int(f.replace(".png", "")))

# 문자열 → 파일명 목록 매핑
string_map = defaultdict(list)

for fname in files:
    if not fname.endswith(".png"):
        continue
    arr = np.array(Image.open(os.path.join("processed_data", fname)).convert("RGB"))
    s = pixels_to_string(arr)
    string_map[s].append(fname)

# 겹치는 항목 출력
duplicates = {k: v for k, v in string_map.items() if len(v) > 1}
unique_count = len([v for v in string_map.values() if len(v) == 1])

print(f"전체 파일: {len(files)}개")
print(f"고유 문자열: {len(string_map)}개")
print(f"중복 없는 파일: {unique_count}개")
print(f"중복 그룹: {len(duplicates)}개")

if duplicates:
    print("\n=== 중복 목록 ===")
    for s, fnames in duplicates.items():
        print(f"  {fnames} → '{s[:40]}{'...' if len(s)>40 else ''}'")
else:
    print("\n중복 없음")
