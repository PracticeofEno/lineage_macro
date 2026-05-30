import time

import macro


macro.init_setting("server")
macro.force_set_foreground_window(macro.lineage1_hwnd)
time.sleep(0.5)
macro.arduino_init_cursor()  # Arduino curX/curY를 (0,0)으로 동기화
time.sleep(0.5)
macro.mouse_move(100, 100)
time.sleep(0.5)