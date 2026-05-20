from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass

import win32con

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from macro_common.arduino_tcp import ArduinoProxyClient, ProxyConnectionError
from macro_common.windowing import RenameableWindowTarget, focus_window, move_window_to_origin

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "meat_macro_config.json")
POINT_NAMES = ("start", "end1", "end2", "end3", "end4")
END_POINT_NAMES = POINT_NAMES[1:]
DIRECTION_POINT_NAMES = tuple(f"dir{idx}" for idx in range(1, 9))
DIRECTION_LABELS = {
    1: "north",
    2: "northeast",
    3: "east",
    4: "southeast",
    5: "south",
    6: "southwest",
    7: "west",
    8: "northwest",
}


@dataclass(slots=True)
class TargetConfig:
    window_title_prefix: str
    start_screen_pos: list[int]
    end_screen_positions: list[list[int]]
    direction_screen_positions: list[list[int]]


@dataclass(slots=True)
class MacroConfig:
    proxy_host: str
    proxy_port: int
    cycle_interval_seconds: float
    pre_drag_delay_seconds: float
    post_drag_delay_seconds: float
    between_action_delay_seconds: float
    between_window_delay_seconds: float
    targets: dict[str, TargetConfig]


def _default_config() -> MacroConfig:
    return MacroConfig(
        proxy_host="127.0.0.1",
        proxy_port=9998,
        cycle_interval_seconds=1800.0,
        pre_drag_delay_seconds=0.05,
        post_drag_delay_seconds=0.05,
        between_action_delay_seconds=0.15,
        between_window_delay_seconds=0.30,
        targets={
            "server": TargetConfig(
                window_title_prefix="server",
                start_screen_pos=[0, 0],
                end_screen_positions=[[0, 0] for _ in range(4)],
                direction_screen_positions=[[0, 0] for _ in range(8)],
            ),
            "client": TargetConfig(
                window_title_prefix="client",
                start_screen_pos=[0, 0],
                end_screen_positions=[[0, 0] for _ in range(4)],
                direction_screen_positions=[[0, 0] for _ in range(8)],
            ),
        },
    )


def _normalize_point(value) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return [0, 0]
    return [int(value[0]), int(value[1])]


def _normalize_end_positions(target_raw: dict) -> list[list[int]]:
    raw_positions = target_raw.get("end_screen_positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raw_positions = target_raw.get("end_client_positions")
    if isinstance(raw_positions, list) and raw_positions:
        positions = [_normalize_point(value) for value in raw_positions[:4]]
    else:
        old_end = _normalize_point(target_raw.get("end_client_pos", [0, 0]))
        positions = [old_end[:] for _ in range(4)]

    while len(positions) < 4:
        positions.append([0, 0])
    return positions


def _normalize_direction_positions(target_raw: dict) -> list[list[int]]:
    raw_positions = target_raw.get("direction_screen_positions")
    if isinstance(raw_positions, list) and raw_positions:
        positions = [_normalize_point(value) for value in raw_positions[:8]]
    else:
        positions = []

    while len(positions) < 8:
        positions.append([0, 0])
    return positions


def load_config(path: str) -> MacroConfig:
    if not os.path.exists(path):
        config = _default_config()
        save_config(path, config)
        return config

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    targets: dict[str, TargetConfig] = {}
    for name in ("server", "client"):
        target_raw = raw.get("targets", {}).get(name, {})
        targets[name] = TargetConfig(
            window_title_prefix=str(target_raw.get("window_title_prefix", name)).strip() or name,
            start_screen_pos=_normalize_point(
                target_raw.get("start_screen_pos", target_raw.get("start_client_pos", [0, 0]))
            ),
            end_screen_positions=_normalize_end_positions(target_raw),
            direction_screen_positions=_normalize_direction_positions(target_raw),
        )

    return MacroConfig(
        proxy_host=str(raw.get("proxy_host", "127.0.0.1")).strip() or "127.0.0.1",
        proxy_port=int(raw.get("proxy_port", 9998)),
        cycle_interval_seconds=float(raw.get("cycle_interval_seconds", 1800.0)),
        pre_drag_delay_seconds=float(raw.get("pre_drag_delay_seconds", 0.05)),
        post_drag_delay_seconds=float(raw.get("post_drag_delay_seconds", 0.05)),
        between_action_delay_seconds=float(raw.get("between_action_delay_seconds", 0.15)),
        between_window_delay_seconds=float(raw.get("between_window_delay_seconds", 0.30)),
        targets=targets,
    )


def save_config(path: str, config: MacroConfig) -> None:
    raw = asdict(config)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=True, indent=2)


def _format_proxy_connection_error(exc: OSError, host: str, port: int) -> str:
    command = (
        "python meat_macro\\meat_macro_proxy.py "
        f"--serial-port COMx --host {host} --port {port}"
    )
    details = [
        f"cannot connect to Arduino proxy at {host}:{port}",
        "Start the proxy inside the same VM first.",
        f"Example: {command}",
        "Replace COMx with the Arduino COM port visible inside the VM.",
    ]
    if isinstance(exc, ConnectionRefusedError):
        details.append("The TCP port is reachable on this machine, but nothing is listening on it.")
    elif isinstance(exc, TimeoutError):
        details.append("The TCP connection attempt timed out.")
    else:
        details.append(f"Socket error: {exc}")
    return " ".join(details)


class MeatMacro:
    def __init__(self, config_path: str):
        self._config_path = config_path
        self._config = load_config(config_path)
        self._proxy = self._build_proxy(self._config)
        self._loop_thread: threading.Thread | None = None
        self._loop_stop_event = threading.Event()

    @staticmethod
    def _build_proxy(config: MacroConfig) -> ArduinoProxyClient:
        return ArduinoProxyClient(
            config.proxy_host,
            config.proxy_port,
            connect_error_factory=_format_proxy_connection_error,
        )

    def close(self) -> None:
        self.stop_loop()
        self._proxy.close()

    def reload(self) -> None:
        was_running = self.loop_running
        self.stop_loop()
        self._config = load_config(self._config_path)
        self._proxy.close()
        self._proxy = self._build_proxy(self._config)
        if was_running:
            print("[reload] scheduled loop stopped")

    @property
    def config(self) -> MacroConfig:
        return self._config

    def resolve_target(self, target_name: str) -> tuple[TargetConfig, int, str]:
        target = self._config.targets[target_name]
        resolver = RenameableWindowTarget(target.window_title_prefix)
        hwnd, title = resolver.resolve()
        return target, hwnd, title

    def set_point(self, target_name: str, point_name: str, x: int, y: int) -> None:
        if point_name not in POINT_NAMES:
            raise RuntimeError(f"unknown point: {point_name}")

        target = self._config.targets[target_name]
        screen_pos = [int(x), int(y)]

        if point_name == "start":
            target.start_screen_pos = screen_pos
        else:
            target.end_screen_positions[int(point_name[-1]) - 1] = screen_pos

        save_config(self._config_path, self._config)
        print(f"[set] saved {target_name} {point_name}: {screen_pos}")

    def set_direction_preset(self, target_name: str, direction_index: int, x: int, y: int) -> None:
        if direction_index not in DIRECTION_LABELS:
            raise RuntimeError(f"unknown direction preset: {direction_index}")

        target = self._config.targets[target_name]
        screen_pos = [int(x), int(y)]
        target.direction_screen_positions[direction_index - 1] = screen_pos
        save_config(self._config_path, self._config)
        print(
            f"[set] saved {target_name} dir{direction_index} "
            f"({DIRECTION_LABELS[direction_index]}): {screen_pos}"
        )

    def apply_direction_preset(self, target_name: str, point_name: str, direction_index: int) -> None:
        if point_name not in END_POINT_NAMES:
            raise RuntimeError("direction presets can only be applied to end1-end4")
        if direction_index not in DIRECTION_LABELS:
            raise RuntimeError(f"unknown direction preset: {direction_index}")

        target = self._config.targets[target_name]
        screen_pos = target.direction_screen_positions[direction_index - 1][:]
        target.end_screen_positions[int(point_name[-1]) - 1] = screen_pos
        save_config(self._config_path, self._config)
        print(
            f"[set] saved {target_name} {point_name} from "
            f"dir{direction_index} ({DIRECTION_LABELS[direction_index]}): {screen_pos}"
        )

    def run_target(self, target_name: str) -> None:
        target, hwnd, title = self.resolve_target(target_name)
        rect = move_window_to_origin(hwnd)
        focus_window(hwnd)

        start_screen = tuple(target.start_screen_pos)

        print(
            f"[run] {target_name} -> {title} "
            f"window_rect={rect} "
            f"start={target.start_screen_pos}"
        )

        for idx, end_screen_pos in enumerate(target.end_screen_positions, start=1):
            end_screen = tuple(end_screen_pos)
            self._proxy.init_cursor()
            self._proxy.move_mouse_abs(*start_screen)
            time.sleep(self._config.pre_drag_delay_seconds)
            self._proxy.left_down()
            time.sleep(self._config.pre_drag_delay_seconds)
            self._proxy.move_mouse_abs(*end_screen)
            time.sleep(self._config.post_drag_delay_seconds)
            self._proxy.left_up()
            time.sleep(self._config.post_drag_delay_seconds)
            self._proxy.key_press(ord("1"))
            self._proxy.key_press(win32con.VK_RETURN)
            print(
                f"[run] {target_name} action {idx}/4 "
                f"end={end_screen_pos}"
            )
            if idx < len(target.end_screen_positions):
                time.sleep(self._config.between_action_delay_seconds)

    def run_many(self, target_names: list[str]) -> None:
        for idx, target_name in enumerate(target_names):
            self.run_target(target_name)
            if idx + 1 < len(target_names):
                time.sleep(self._config.between_window_delay_seconds)

    @property
    def loop_running(self) -> bool:
        return self._loop_thread is not None and self._loop_thread.is_alive()

    def start_loop(self, target_names: list[str]) -> None:
        if self.loop_running:
            raise RuntimeError("scheduled loop is already running")

        self._loop_stop_event.clear()
        self._loop_thread = threading.Thread(
            target=self._loop_worker,
            args=(list(target_names),),
            daemon=True,
        )
        self._loop_thread.start()
        print(
            f"[loop] started for {', '.join(target_names)} "
            f"every {self._config.cycle_interval_seconds:.0f}s"
        )

    def stop_loop(self) -> None:
        if not self.loop_running:
            return
        self._loop_stop_event.set()
        assert self._loop_thread is not None
        self._loop_thread.join()
        self._loop_thread = None
        print("[loop] stopped")

    def _loop_worker(self, target_names: list[str]) -> None:
        while not self._loop_stop_event.is_set():
            cycle_started = time.monotonic()
            started_text = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[loop] cycle started at {started_text}")
            try:
                self.run_many(target_names)
            except Exception as exc:
                print(f"[loop] cycle error: {exc}")

            wait_seconds = self._config.cycle_interval_seconds - (time.monotonic() - cycle_started)
            if wait_seconds <= 0:
                continue

            next_run_text = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() + wait_seconds),
            )
            print(f"[loop] next cycle at {next_run_text}")
            if self._loop_stop_event.wait(wait_seconds):
                break

    def print_status(self) -> None:
        print(f"[status] proxy={self._config.proxy_host}:{self._config.proxy_port}")
        print(f"[status] proxy_connection={self._proxy.probe()}")
        print(f"[status] cycle_interval_seconds={self._config.cycle_interval_seconds}")
        print(f"[status] loop_running={self.loop_running}")
        for name in ("server", "client"):
            target = self._config.targets[name]
            try:
                _, hwnd, title = self.resolve_target(name)
                window_info = f"{title} ({hwnd})"
            except Exception as exc:
                window_info = f"unresolved ({exc})"
            print(
                f"[status] {name}: "
                f"prefix={target.window_title_prefix}, "
                f"start={target.start_screen_pos}, "
                f"ends={target.end_screen_positions}, "
                f"dirs={target.direction_screen_positions}, "
                f"window={window_info}"
            )


def parse_direction_index(value: str) -> int:
    cleaned = value.strip().lower()
    if cleaned.startswith("dir"):
        cleaned = cleaned[3:]
    index = int(cleaned)
    if index not in DIRECTION_LABELS:
        raise RuntimeError("direction preset must be 1..8")
    return index


def parse_target_names(value: str) -> list[str]:
    if value == "all":
        return ["server", "client"]
    if value not in {"server", "client"}:
        raise RuntimeError(f"unknown target: {value}")
    return [value]


def print_help() -> None:
    print("Commands")
    print("  status")
    print("  set start server <x> <y>")
    print("  set dir1 server <x> <y>")
    print("  set dir8 client <x> <y>")
    print("  set end1 server <x> <y>")
    print("  set end1 server <1-8>")
    print("  set end2 server <x> <y>")
    print("  set end2 server <1-8>")
    print("  set end3 server <x> <y>")
    print("  set end3 server <1-8>")
    print("  set end4 server <x> <y>")
    print("  set end4 server <1-8>")
    print("  set start client <x> <y>")
    print("  set end1 client <x> <y>")
    print("  set end2 client <x> <y>")
    print("  set end3 client <x> <y>")
    print("  set end4 client <x> <y>")
    print("  1=north 2=northeast 3=east 4=southeast 5=south 6=southwest 7=west 8=northwest")
    print("  run server")
    print("  run client")
    print("  run all")
    print("  loop start server")
    print("  loop start client")
    print("  loop start all")
    print("  loop stop")
    print("  reload")
    print("  quit")


def run_shell(app: MeatMacro) -> int:
    print_help()
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not raw:
            continue

        try:
            parts = raw.split()
            if parts == ["status"]:
                app.print_status()
            elif parts == ["reload"]:
                app.reload()
                print("[reload] config reloaded")
            elif parts == ["loop", "stop"]:
                app.stop_loop()
            elif parts == ["quit"] or parts == ["exit"]:
                return 0
            elif len(parts) == 5 and parts[0] == "set" and parts[1] in POINT_NAMES:
                app.set_point(parts[2], parts[1], int(parts[3]), int(parts[4]))
            elif len(parts) == 5 and parts[0] == "set" and parts[1] in DIRECTION_POINT_NAMES:
                app.set_direction_preset(parts[2], parse_direction_index(parts[1]), int(parts[3]), int(parts[4]))
            elif len(parts) == 4 and parts[0] == "set" and parts[1] in END_POINT_NAMES:
                app.apply_direction_preset(parts[2], parts[1], parse_direction_index(parts[3]))
            elif len(parts) == 2 and parts[0] == "run":
                app.run_many(parse_target_names(parts[1]))
            elif len(parts) == 3 and parts[0] == "loop" and parts[1] == "start":
                app.start_loop(parse_target_names(parts[2]))
            elif parts == ["help"]:
                print_help()
            else:
                print("[error] unknown command")
                print_help()
        except Exception as exc:
            print(f"[error] {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drag using screen coordinates, then press 1 and Enter, repeated for server/client windows."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the JSON config file.",
    )
    parser.add_argument(
        "--set",
        choices=POINT_NAMES + DIRECTION_POINT_NAMES,
        help="Set a point directly by absolute screen coordinates or save dir1-dir8 presets.",
    )
    parser.add_argument(
        "--target",
        choices=["server", "client"],
        help="Target window for --set or --run.",
    )
    parser.add_argument(
        "--x",
        type=int,
        help="X coordinate for --set.",
    )
    parser.add_argument(
        "--y",
        type=int,
        help="Y coordinate for --set.",
    )
    parser.add_argument(
        "--preset",
        type=int,
        help="Apply direction preset 1..8 to end1-end4 during --set.",
    )
    parser.add_argument(
        "--run",
        choices=["server", "client", "all"],
        help="Run the macro once for the selected target set.",
    )
    parser.add_argument(
        "--loop",
        choices=["server", "client", "all"],
        help="Run the macro repeatedly on a schedule until stopped.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = MeatMacro(args.config)
    try:
        if args.set:
            if not args.target:
                raise SystemExit("--set requires --target")
            if args.set in DIRECTION_POINT_NAMES:
                if args.x is None or args.y is None:
                    raise SystemExit("--set dir1-dir8 requires --x and --y")
                app.set_direction_preset(args.target, parse_direction_index(args.set), args.x, args.y)
            elif args.preset is not None:
                app.apply_direction_preset(args.target, args.set, args.preset)
            else:
                if args.x is None or args.y is None:
                    raise SystemExit("--set requires --x and --y")
                app.set_point(args.target, args.set, args.x, args.y)
            return 0

        if args.run:
            app.run_many(parse_target_names(args.run))
            return 0

        if args.loop:
            app.start_loop(parse_target_names(args.loop))
            try:
                while app.loop_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                print()
                app.stop_loop()
            return 0

        return run_shell(app)
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
