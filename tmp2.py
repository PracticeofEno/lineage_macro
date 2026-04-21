import macro
import time
import numpy as np
from PIL import Image, ImageDraw

def main() -> None:
    macro.init_setting("server")
    macro.calibrate_hid_scale()
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    time.sleep(1)
    macro.turn_east()


if __name__ == "__main__":
    main()
