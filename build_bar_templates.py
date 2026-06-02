"""screenshots/ 의 라벨(우상단 상태창 OCR)을 이용해 하단 HP/MP 바 숫자 사전을
생성한다. 게이지가 줄어 글자 뒤 배경이 빨강→어두움으로 바뀌어도 글자만 뽑히도록
'글자색 = min(R,G,B) >= thr' 범위 마스크로 셀의 픽셀 좌표문자열을 만들고,
{좌표문자열: 숫자} 사전으로 저장한다. 런타임은 dict.get() 한 번으로 조회한다.

데이터가 많을수록(게이지 경계가 글자를 지나는 위치를 촘촘히 덮을수록) 정확도가
높아진다. 초록(중독) 프레임은 제외한다.

실행: python build_bar_templates.py  -> bar_templates.json 생성
"""
import glob
import json
import collections

import numpy as np
from PIL import Image

import macro

# ── 바 셀 그리드 (고정) ───────────────────────────────────────────────────────
# HP: 우측정렬. 현재값 1의자리 셀 x=487, 최대값 1의자리 셀 x=527 (왼쪽으로 -10).
# MP: 좌측정렬. 현재값 첫자리 셀 x=717 (오른쪽으로 +10), '/' 뒤에 최대값.
#
# [글자 분리 = 코어색 박스]  글자의 불투명 코어색은 배경(빨강/어두움)·게이지 잔량과
# 무관하게 일정하다(HP 흰빛분홍, MP 흰빛하늘). 그 코어색 ±tol 박스만 잡으면 가장자리
# 안티에일리어싱 변동이 빠져, 숫자 하나가 (거의) 좌표문자열 1개로 모인다.
HP = dict(y0=667, h=15, center=(255, 213, 213), tol=5)
MP = dict(y0=667, h=14, center=(200, 206, 255), tol=5)


def status_pair(im, y, color):
    """우상단 상태창에서 (현재, 최대) 숫자 문자열을 읽는다."""
    for dx in (0, 5, 10):
        c = macro.crop(im, 976 + dx, y, 120, 21)
        t = macro.read_text(c, 0, 0, color)
        if '/' in t:
            a, b = t.split('/', 1)
            a = ''.join(ch for ch in a if ch.isdigit())
            b = ''.join(ch for ch in b if ch.isdigit())
            if a and b:
                return a, b
    return None, None


def cell_coordstr(arr, cx, cfg):
    """셀(cx) 내부에서 글자 코어색(center ±tol 박스) 픽셀 좌표를 한 줄 문자열로."""
    sub = arr[cfg["y0"]:cfg["y0"] + cfg["h"], cx:cx + 10].astype(int)
    cr, cg, cb = cfg["center"]
    tol = cfg["tol"]
    m = ((np.abs(sub[:, :, 0] - cr) <= tol)
         & (np.abs(sub[:, :, 1] - cg) <= tol)
         & (np.abs(sub[:, :, 2] - cb) <= tol))
    ys, xs = np.where(m)
    return "".join(f"{x}{y}" for x, y in sorted(zip(xs.tolist(), ys.tolist())))


def is_green_hp_bar(arr) -> bool:
    """HP 바 배경이 초록(중독 상태)인지 판정한다. 사용자 환경에선 HP가 초록으로
    변하지 않으므로 이런 프레임은 템플릿 학습에서 제외한다."""
    sub = arr[668:680, 455:535].reshape(-1, 3).astype(int)
    lum = np.minimum(np.minimum(sub[:, 0], sub[:, 1]), sub[:, 2])
    bg = sub[lum < 150]  # 글자(밝은 흰빛) 제외한 배경 픽셀
    if len(bg) == 0:
        return False
    r, g, b = bg.mean(axis=0)
    return g > r + 25 and g > b + 15


def main():
    files = sorted(glob.glob('screenshots/*.png'))
    records = []
    skipped_green = 0
    for f in files:
        im = Image.open(f).convert('RGB')
        arr = np.array(im)
        if is_green_hp_bar(arr):  # 초록(중독) 프레임은 데이터에서 제외
            skipped_green += 1
            continue
        hc, hm = status_pair(im, 71, (247, 201, 227))
        mc, mm = status_pair(im, 96, (204, 227, 255))
        records.append((f, arr, hc, hm, mc, mm))

    # coordstr -> Counter(digit): 같은 좌표문자열이 여러 숫자로 라벨되면(충돌) 경고.
    hp_raw = collections.defaultdict(collections.Counter)
    mp_raw = collections.defaultdict(collections.Counter)
    for f, arr, hc, hm, mc, mm in records:
        if hc and hm:
            for i, d in enumerate(hc[::-1]):
                hp_raw[cell_coordstr(arr, 487 - 10 * i, HP)][d] += 1
            for i, d in enumerate(hm[::-1]):
                hp_raw[cell_coordstr(arr, 527 - 10 * i, HP)][d] += 1
        if mc and mm:
            for i, d in enumerate(mc):
                mp_raw[cell_coordstr(arr, 717 + 10 * i, MP)][d] += 1
            for i, d in enumerate(mm):
                mp_raw[cell_coordstr(arr, 717 + 10 * (len(mc) + 1 + i), MP)][d] += 1

    # {coordstr: digit} 사전으로 확정 (충돌 시 최다 득표 숫자 채택).
    def finalize(raw, name):
        digits, collisions = {}, 0
        for s, cnt in raw.items():
            if not s:
                continue
            if len(cnt) > 1:
                collisions += 1
                print(f"  [{name} 충돌] {dict(cnt)} (len={len(s)}) -> '{cnt.most_common(1)[0][0]}' 채택")
            digits[s] = cnt.most_common(1)[0][0]
        return digits, collisions

    hp_digits, hp_coll = finalize(hp_raw, "HP")
    mp_digits, mp_coll = finalize(mp_raw, "MP")

    out = {
        "HP": {"y0": HP["y0"], "h": HP["h"], "center": list(HP["center"]), "tol": HP["tol"], "digits": hp_digits},
        "MP": {"y0": MP["y0"], "h": MP["h"], "center": list(MP["center"]), "tol": MP["tol"], "digits": mp_digits},
    }
    with open("bar_templates.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False)
    print(f"[build] HP 좌표문자열 {len(hp_digits)}개(충돌 {hp_coll}), "
          f"MP {len(mp_digits)}개(충돌 {mp_coll}), "
          f"초록 프레임 {skipped_green}개 제외 -> bar_templates.json")


if __name__ == "__main__":
    main()
