import time
import threading
import macro
import ocr
from tools import imageProcesser


if __name__ == "__main__":
    macro.init_lineage_windows()
    macro.init_mouse_x_y()

    print("\n명령어: q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            break
        if cmd == "1":
            while True:
                macro.accept_exchange_and_track_adena()
        if cmd == "2":
            img = macro.screenshot()
            img.save("screenshot.png")
        if cmd == "3":
            img = macro.screenshot()
            cropped = imageProcesser.crop(img, 717, 667, 100, 21)
            results = ocr.ocr(cropped, ['en'])
            text = ' '.join(t for _, t, _ in results)
            # if '/' not in text:
            #     return 0
            before_slash = text.split('/')[0].strip()
            mp = int(''.join(c for c in before_slash if c.isdigit()) or 0)
            print(mp)
