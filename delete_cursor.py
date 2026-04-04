from PIL import Image
import numpy as np
import os

TARGET_DIRS = [
    r"C:\Users\eno\test\processed_data",
    r"C:\Users\eno\test\tmp_add_processed",
]

def remove_cursors(data_dir):
    if not os.path.isdir(data_dir):
        print(f"디렉토리 없음: {data_dir}")
        return []

    files = sorted(
        [f for f in os.listdir(data_dir) if f.endswith(".png")],
        key=lambda f: int(f.split(".")[0]) if f.split(".")[0].isdigit() else f
    )

    removed = []

    for fname in files:
        path = os.path.join(data_dir, fname)
        img = Image.open(path)
        arr = np.array(img)

        red = (arr[:,:,0] > 200) & (arr[:,:,1] < 50) & (arr[:,:,2] < 50)
        col_px = red.sum(axis=0)
        active_cols = np.where(col_px > 0)[0]

        if len(active_cols) == 0:
            continue

        cmax = int(active_cols[-1])

        # 커서 감지: 가장 오른쪽 열이 거의 풀 높이(>=11px)이고 바로 왼쪽 열이 비어있음
        if col_px[cmax] >= 11 and (cmax == 0 or col_px[cmax - 1] == 0):
            arr[:, cmax] = [0, 0, 0]  # 커서 열을 배경(검정)으로 덮어씀
            Image.fromarray(arr).save(path)
            removed.append(fname)

    return removed

for d in TARGET_DIRS:
    removed = remove_cursors(d)
    print(f"[{d}] 커서 제거: {len(removed)}개")
    for f in removed:
        print(f"  {f}")
