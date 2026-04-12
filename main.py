import threading
import macro
import ocr
import imageProcesser

running = True

if __name__ == "__main__":
    macro.init_setting("server")

    print("\n명령어: q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            break
        if cmd == "1":
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            running = True
            def _exchange_loop():
                while running:
                    macro.accept_exchange_and_track_adena()
            threading.Thread(target=_exchange_loop, daemon=True).start()
        if cmd == "2":
            running = False
        if cmd == "3":
            img = macro.screenshot()
            img.save("screenshot.png")
        if cmd == "4":
            img = macro.screenshot()
            cropped = imageProcesser.crop(img, 717, 667, 100, 21)
            results = ocr.ocr(cropped, ['en'])
            text = ' '.join(t for _, t, _ in results)
            before_slash = text.split('/')[0].strip()
            mp = int(''.join(c for c in before_slash if c.isdigit()) or 0)
            print(mp)
