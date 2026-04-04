import sys
from PIL import Image
import numpy as np


def all_chars():
    """a-z, A-Z, 한글 음절(가~힣) 순서로 모든 문자를 yield한다."""
    for c in range(ord('a'), ord('z') + 1):
        yield chr(c)
    for c in range(ord('A'), ord('Z') + 1):
        yield chr(c)
    for c in range(0xAC00, 0xD7A4):
        yield chr(c)


if __name__ == "__main__":
    LOW  = (95, 90, 80)
    HIGH = (255, 255, 255)

    path = sys.argv[1] if len(sys.argv) > 1 else "0.png"

    arr = np.array(Image.open(path).convert("RGB"))
    mask = (
        (arr[:,:,0] >= LOW[0]) & (arr[:,:,0] <= HIGH[0]) &
        (arr[:,:,1] >= LOW[1]) & (arr[:,:,1] <= HIGH[1]) &
        (arr[:,:,2] >= LOW[2]) & (arr[:,:,2] <= HIGH[2])
    )

    print(f"{path} → 글자 픽셀: {mask.sum()} / {mask.size}")

    arr[mask] = [255, 0, 0]
    result = Image.fromarray(arr)
    result = result.resize((result.width * 10, result.height * 10), Image.NEAREST)
    result.show()
