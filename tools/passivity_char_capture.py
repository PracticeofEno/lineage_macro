import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import macro
import imageProcesser

macro.set_hwnd(53022452)
macro.move_window(0, 0)

img = macro.screenshot()
cropped = imageProcesser.crop(img, 155, 583, 50, 15)

os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "image"), exist_ok=True)
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "image", "passivity_char_capture.png")
cropped.save(out_path)
print(f"저장 완료: {out_path}")
