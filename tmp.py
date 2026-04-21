from PIL import Image
import imageProcesser
import numpy as np

def count_black(image: Image.Image) -> int:
    arr = np.array(image.convert("RGB"))
    mask = (arr[:,:,0] == 0) & (arr[:,:,1] == 0) & (arr[:,:,2] == 0)
    return int(mask.sum())

x = 853 - 5
y = 906 - 31
w = 5
h = 5
img = Image.open("aa.png")
cropped = imageProcesser.crop(img, x, y, w, h)

print(count_black(cropped))
cropped.show()
