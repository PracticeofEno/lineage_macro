from PIL import Image
import numpy as np
import os

data_dir = r"C:\Users\eno\test\processed_data"

def analyze(i):
    arr = np.array(Image.open(os.path.join(data_dir, f"{i}.png")))
    red = (arr[:,:,0] > 200) & (arr[:,:,1] < 50) & (arr[:,:,2] < 50)

    col_px = red.sum(axis=0)
    active_cols = np.where(col_px > 0)[0]
    if len(active_cols) == 0:
        return None

    cmin, cmax = int(active_cols[0]), int(active_cols[-1])

    # 커서 감지: 가장 오른쪽 활성 열이 full-height (12px)이고 바로 왼쪽 열이 비어있음
    cursor_col = None
    if col_px[cmax] >= 11 and (cmax == 0 or col_px[cmax - 1] == 0):
        cursor_col = cmax
        # 커서 열 제거
        char_red = red.copy()
        char_red[:, cursor_col] = False
        active_cols_char = np.where(char_red.any(axis=0))[0]
    else:
        char_red = red
        active_cols_char = active_cols

    if len(active_cols_char) == 0:
        return {"i": i, "cursor": cursor_col is not None, "char_w": 0, "char_h": 0}

    rows_char = np.where(char_red.any(axis=1))[0]
    cmin_c, cmax_c = int(active_cols_char[0]), int(active_cols_char[-1])
    rmin_c, rmax_c = int(rows_char[0]), int(rows_char[-1])
    char_w = cmax_c - cmin_c + 1
    char_h = rmax_c - rmin_c + 1

    return {
        "i": i, "cursor": cursor_col is not None,
        "char_w": char_w, "char_h": char_h,
        "rmin": rmin_c, "rmax": rmax_c
    }

print(f"{'idx':>4} {'cursor':>7} {'char_w':>7} {'char_h':>7}  {'rows':>10}")
print("-" * 50)
for i in range(52):
    r = analyze(i)
    cursor_mark = "YES" if r["cursor"] else "-"
    print(f"[{r['i']:2d}]  {cursor_mark:>6}    w={r['char_w']:2d}    h={r['char_h']:2d}  rows {r['rmin']}-{r['rmax']}")
