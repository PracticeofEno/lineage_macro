import time
import threading
import macro
import ocr


if __name__ == "__main__":
    macro.init_lineage_windows()
    macro.init_mouse_x_y((689, 386), (1369, 396))

    print("\n명령어: q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            break
        if cmd == "1":
            while True:
                macro.accept_exchange_and_track_adena()
