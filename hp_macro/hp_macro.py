import argparse
from collections import Counter
import json
import sys
import time
from pathlib import Path
from typing import Any

import win32con
import win32gui
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macro

DEFAULT_CONFIG = Path(__file__).with_name("config.json")


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data


def parse_current_value(text: str) -> int | None:
    current = text.split("/", 1)[0]
    digits = "".join(ch for ch in current if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def parse_hp_values(text: str) -> tuple[int, int] | None:
    if "/" not in text:
        return None

    current_text, maximum_text = text.split("/", 1)
    current_digits = "".join(ch for ch in current_text if ch.isdigit())
    maximum_digits = "".join(ch for ch in maximum_text if ch.isdigit())
    if not current_digits or not maximum_digits:
        return None

    current = int(current_digits)
    maximum = int(maximum_digits)
    if maximum <= 0:
        return None
    return current, maximum


def format_rgb(color: tuple[int, int, int]) -> str:
    r, g, b = color
    return f"({r}, {g}, {b}) #{r:02X}{g:02X}{b:02X}"


def find_window(title_prefix: str | None) -> int:
    windows: list[tuple[str, int]] = []

    def callback(hwnd: int, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            windows.append((title, hwnd))

    win32gui.EnumWindows(callback, None)

    if title_prefix:
        for title, hwnd in windows:
            if title.startswith(title_prefix):
                return hwnd
        raise RuntimeError(f"window not found: {title_prefix}")

    for preferred in ("client", "server"):
        for title, hwnd in windows:
            if title == preferred:
                return hwnd

    for title, hwnd in windows:
        if title.startswith("Lineage Classic"):
            return hwnd

    raise RuntimeError("Lineage Classic window not found")


def translate_capture_xy(x: int, y: int, read_config: dict[str, Any]) -> tuple[int, int]:
    coordinate_mode = str(read_config.get("coordinate_mode", "screen")).lower()
    capture_mode = str(read_config.get("capture_mode", "window")).lower()

    if coordinate_mode == capture_mode:
        return x, y

    left, top, _right, _bottom = win32gui.GetWindowRect(macro.lineage1_hwnd)
    if coordinate_mode == "screen" and capture_mode == "window":
        return x - left, y - top
    if coordinate_mode == "window" and capture_mode == "screen":
        return x + left, y + top

    raise RuntimeError(f"unsupported coordinate/capture mode: {coordinate_mode}/{capture_mode}")


def capture_area(img, read_config: dict[str, Any], x: int, y: int, width: int, height: int):
    capture_mode = str(read_config.get("capture_mode", "window")).lower()
    crop_x, crop_y = translate_capture_xy(x, y, read_config)

    if capture_mode == "screen":
        return ImageGrab.grab(bbox=(crop_x, crop_y, crop_x + width, crop_y + height)).convert("RGB")
    if capture_mode == "window":
        if img is None:
            img = macro.screenshot(hwnd=macro.lineage1_hwnd)
        return macro.crop(img, crop_x, crop_y, width, height)
    raise RuntimeError(f"unknown capture_mode: {capture_mode}")


def capture_hp_area(img, hp_read: dict[str, Any], x: int, y: int, width: int, height: int):
    return capture_area(img, hp_read, x, y, width, height)


def uses_window_capture(config: dict[str, Any]) -> bool:
    for read_key in ("hp_read", "mp_read"):
        read_config = config.get(read_key)
        if read_config and str(read_config.get("capture_mode", "window")).lower() == "window":
            return True
    return False


def read_area_text(config: dict[str, Any], read_key: str, img=None) -> str:
    read_config = config[read_key]
    x = int(read_config["x"])
    y = int(read_config["y"])
    width = int(read_config["width"])
    height = int(read_config["height"])
    color = tuple(int(v) for v in read_config["color_rgb"])
    x_offsets = read_config.get("x_offsets", [0])
    text_x_offsets = read_config.get("text_x_offsets", [0])
    text_y_offsets = read_config.get("text_y_offsets", [0])
    best = ""

    for dx in x_offsets:
        cropped = capture_area(img, read_config, x + int(dx), y, width, height)
        for ty in text_y_offsets:
            for tx in text_x_offsets:
                text = macro.read_text(cropped, int(tx), int(ty), color)
                if len(text) > len(best):
                    best = text
    return best


def read_hp_text(config: dict[str, Any], img=None) -> str:
    return read_area_text(config, "hp_read", img=img)


def read_mp_text(config: dict[str, Any], img=None) -> str:
    return read_area_text(config, "mp_read", img=img)


def read_hp(config: dict[str, Any], img=None) -> int | None:
    text = read_hp_text(config, img=img)
    hp = parse_current_value(text)
    if hp is not None:
        return hp
    return None


def read_stat_state(text: str) -> dict[str, float | int | str] | None:
    values = parse_hp_values(text)
    if values is None:
        return None

    current, maximum = values
    percent = current / maximum * 100.0
    return {
        "text": text,
        "current": current,
        "maximum": maximum,
        "percent": percent,
    }


def read_hp_state(config: dict[str, Any], img=None) -> dict[str, float | int | str] | None:
    return read_stat_state(read_hp_text(config, img=img))


def read_mp_state(config: dict[str, Any], img=None) -> dict[str, float | int | str] | None:
    return read_stat_state(read_mp_text(config, img=img))


def sample_hp_colors(config: dict[str, Any], limit: int) -> None:
    hp_read = config["hp_read"]
    x = int(hp_read["x"])
    y = int(hp_read["y"])
    width = int(hp_read["width"])
    height = int(hp_read["height"])
    img = macro.screenshot(hwnd=macro.lineage1_hwnd) if uses_window_capture(config) else None
    cropped = capture_hp_area(img, hp_read, x, y, width, height).convert("RGB")
    counts = Counter(cropped.getdata())

    capture_mode = str(hp_read.get("capture_mode", "window")).lower()
    coordinate_mode = str(hp_read.get("coordinate_mode", "screen")).lower()
    print(
        f"[hp_macro] sample area x={x}, y={y}, width={width}, height={height}, "
        f"coordinate_mode={coordinate_mode}, capture_mode={capture_mode}"
    )
    print("[hp_macro] top colors:")
    for color, count in counts.most_common(limit):
        print(f"  {format_rgb(color)} count={count}")

    vivid = [
        (color, count)
        for color, count in counts.items()
        if max(color) >= 120 and max(color) - min(color) >= 25
    ]
    vivid.sort(key=lambda item: item[1], reverse=True)

    print("[hp_macro] bright/vivid candidates:")
    for color, count in vivid[:limit]:
        print(f"  {format_rgb(color)} count={count}")


def hold_f5(seconds: float) -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_down(win32con.VK_F5)
    try:
        time.sleep(seconds)
    finally:
        macro.arduino_key_up(win32con.VK_F5)


def press_f8() -> None:
    macro.force_set_foreground_window(macro.lineage1_hwnd)
    macro.arduino_key_press(win32con.VK_F8)


def init_window(config: dict[str, Any], title_override: str | None) -> None:
    title = title_override if title_override is not None else str(config.get("window_title", "client"))
    macro.set_hwnd(find_window(title))


def run(config: dict[str, Any], once: bool = False) -> None:
    hp_threshold = float(config.get("hp_percent_threshold", 50.0))
    mp_threshold = float(config.get("mp_percent_threshold", 30.0))
    poll_interval = float(config.get("poll_interval_seconds", 0.2))
    hold_seconds = float(config.get("f5_hold_seconds", 1.5))
    f8_cooldown = float(config.get("f8_cooldown_seconds", 600.0))
    cooldown = float(config.get("trigger_cooldown_seconds", hold_seconds))
    status_interval = float(config.get("status_interval_seconds", 1.0))
    last_trigger_time = 0.0
    last_status_time = 0.0
    last_f8_time = 0.0

    print(
        f"[hp_macro] start hp_threshold={hp_threshold:.1f}%, mp_threshold={mp_threshold:.1f}%, "
        f"hold={hold_seconds}s, poll={poll_interval}s, cooldown={cooldown}s, "
        f"f8_cooldown={f8_cooldown}s"
    )

    while True:
        img = macro.screenshot(hwnd=macro.lineage1_hwnd) if uses_window_capture(config) else None
        hp_state = read_hp_state(config, img=img)
        mp_state = read_mp_state(config, img=img)
        now = time.time()

        if now - last_status_time >= status_interval:
            if hp_state is None and mp_state is None:
                print("[hp_macro] HP read failed, MP read failed")
            elif hp_state is None:
                mp = int(mp_state["current"])
                mp_maximum = int(mp_state["maximum"])
                mp_percent = float(mp_state["percent"])
                print(f"[hp_macro] HP read failed, MP={mp}/{mp_maximum} ({mp_percent:.1f}%)")
            elif mp_state is None:
                hp = int(hp_state["current"])
                maximum = int(hp_state["maximum"])
                percent = float(hp_state["percent"])
                print(f"[hp_macro] HP={hp}/{maximum} ({percent:.1f}%), MP read failed")
            else:
                hp = int(hp_state["current"])
                maximum = int(hp_state["maximum"])
                percent = float(hp_state["percent"])
                mp = int(mp_state["current"])
                mp_maximum = int(mp_state["maximum"])
                mp_percent = float(mp_state["percent"])
                print(
                    f"[hp_macro] HP={hp}/{maximum} ({percent:.1f}%), "
                    f"MP={mp}/{mp_maximum} ({mp_percent:.1f}%)"
                )
            last_status_time = now

        if once:
            return

        if mp_state is not None:
            mp_percent = float(mp_state["percent"])
            if mp_percent < mp_threshold and now - last_f8_time >= f8_cooldown:
                print(f"[hp_macro] MP {mp_percent:.1f}% < {mp_threshold:.1f}% -> press F8")
                press_f8()
                last_f8_time = time.time()

        if hp_state is not None:
            percent = float(hp_state["percent"])
            if percent < hp_threshold and now - last_trigger_time >= cooldown:
                print(f"[hp_macro] HP {percent:.1f}% < {hp_threshold:.1f}% -> hold F5 {hold_seconds}s")
                hold_f5(hold_seconds)
                last_trigger_time = time.time()

        time.sleep(poll_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read HP and hold F5 when HP is below threshold.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to hp_macro config JSON.")
    parser.add_argument("--percent-threshold", type=float, help="Override hp_percent_threshold from config.")
    parser.add_argument("--threshold", type=float, help="Alias for --percent-threshold.")
    parser.add_argument("--title", help="Window title prefix to target, for example client or server.")
    parser.add_argument("--once", action="store_true", help="Read HP once and exit.")
    parser.add_argument("--sample-colors", action="store_true", help="Print RGB color candidates from the configured HP area.")
    parser.add_argument("--sample-limit", type=int, default=20, help="Number of colors to print with --sample-colors.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    if args.percent_threshold is not None:
        config["hp_percent_threshold"] = args.percent_threshold
    elif args.threshold is not None:
        config["hp_percent_threshold"] = args.threshold

    init_window(config, args.title)
    if args.sample_colors:
        sample_hp_colors(config, args.sample_limit)
        return 0

    try:
        run(config, once=args.once)
    except KeyboardInterrupt:
        print("\n[hp_macro] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
