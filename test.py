from PIL import Image
import numpy as np

THRESHOLD = 170

arr = np.array(Image.open("44.png").convert("RGB"))
brightness = arr.mean(axis=2)
mask = brightness > THRESHOLD

arr[mask] = [255, 0, 0]

result = Image.fromarray(arr)
result = result.resize((result.width * 10, result.height * 10), Image.NEAREST)
result.show()
