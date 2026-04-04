from PIL import Image
import numpy as np
import os

LOW  = (95, 90, 80)
HIGH = (255, 255, 255)

os.makedirs("tmp_add", exist_ok=True)

files = [f for f in os.listdir("tmp_add") if f.endswith(".png")]
for fname in files:
    arr = np.array(Image.open(os.path.join("tmp_add", fname)).convert("RGB"))
    mask = (
        (arr[:,:,0] >= LOW[0]) & (arr[:,:,0] <= HIGH[0]) &
        (arr[:,:,1] >= LOW[1]) & (arr[:,:,1] <= HIGH[1]) &
        (arr[:,:,2] >= LOW[2]) & (arr[:,:,2] <= HIGH[2])
    )
    arr[mask] = [255, 0, 0]
    Image.fromarray(arr).save(os.path.join("tmp_add_processed", fname))

print(f"완료: {len(files)}개 파일 처리됨")
