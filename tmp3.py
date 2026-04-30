"""
tmp3.py - 드래그앤드롭 테스트
source (x, y) → destinations[0..3] 순차 드래그앤드롭
"""

import time
import macro


def arduino_drag_and_drop(x1: int, y1: int, x2: int, y2: int):
    """(x1, y1)에서 (x2, y2)로 드래그앤드롭."""
    macro._arduino_send(f'DD,{x1},{y1},{x2},{y2}')


def drag_to_destinations(source: tuple[int, int], destinations: list[tuple[int, int]], delay: float = 0.5):
    """source 좌표에서 destinations 각각으로 순차 드래그앤드롭."""
    sx, sy = source
    for i, (dx, dy) in enumerate(destinations):
        print(f"[drag] {i+1}/{len(destinations)}: ({sx},{sy}) → ({dx},{dy})")
        arduino_drag_and_drop(sx, sy, dx, dy)
        time.sleep(delay)


if __name__ == "__main__":
    macro.init_setting("server")
    macro.arduino_init_cursor()
    time.sleep(0.5)

    # 드래그 출발지
    source = (300, 400)

    # 드래그 목적지 4개
    destinations = [
        (500, 300),
        (600, 400),
        (500, 500),
        (400, 400),
    ]

    macro.force_set_foreground_window(macro.lineage1_hwnd)
    time.sleep(0.3)

    drag_to_destinations(source, destinations, delay=0.8)
    print("[drag] 완료")
