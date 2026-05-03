import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
import random
import subprocess
import sys
import threading
import time

import win32con

import macro


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DATA_PATH = os.path.join(BASE_DIR, "macro_data.json")
INDEPENDENT_HASTE_CONFIG_PATH = os.path.join(BASE_DIR, "independent_haste_config.json")
INPUT_LOCK_PATH = os.path.join(BASE_DIR, ".independent_haste.lock")

LOW_MP_AVAILABLE_THRESHOLD = 4
POTION_COOLDOWN_SECONDS = 600.0
HASTE_CHECK_DEFAULT_INTERVAL = 3.0
DIRECTION_RETURN_CHECK_INTERVAL = 30.0
EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD = 120.0
EXCHANGE_PIXEL_CHANGE_DEFAULT_XY = (848, 877)
SAME_PICKUP_DELAY_SECONDS = 1.0
SAME_NICKNAME_TURN_DEFAULT_SECONDS = 0.0
STATUS_INTERVAL_DEFAULT_SECONDS = 3.0
DUAL_START_DELAY_DEFAULT_SECONDS = 2.0

TURN_DIRECTIONS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)


def load_macro_data() -> dict:
    with open(MACRO_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_macro_data(data: dict) -> None:
    tmp_path = f"{MACRO_DATA_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, MACRO_DATA_PATH)


def load_independent_haste_config() -> dict:
    if not os.path.exists(INDEPENDENT_HASTE_CONFIG_PATH):
        return {}

    try:
        with open(INDEPENDENT_HASTE_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[config] failed to read independent_haste_config.json: {exc}; using defaults")
        return {}

    if not isinstance(data, dict):
        print("[config] independent_haste_config.json must contain a JSON object; using defaults")
        return {}
    return data


def normalize_nickname_list(raw_nicknames) -> list[str]:
    if isinstance(raw_nicknames, str):
        nicknames = [raw_nicknames]
    elif isinstance(raw_nicknames, (list, tuple, set)):
        nicknames = raw_nicknames
    else:
        nicknames = []

    normalized: list[str] = []
    seen: set[str] = set()
    for nickname in nicknames:
        value = str(nickname).strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def read_direction_change_nicknames(data: dict) -> set[str]:
    raw_nicknames = data.get("direction_change_nicknames", data.get("direction_change_nickname", []))
    return set(normalize_nickname_list(raw_nicknames))


def add_direction_change_nickname(nickname: str) -> bool:
    nickname = str(nickname).strip()
    if not nickname:
        return False

    with input_lock():
        data = load_macro_data()
        nicknames = normalize_nickname_list(
            data.get("direction_change_nicknames", data.get("direction_change_nickname", []))
        )
        if nickname in nicknames:
            return False

        nicknames.append(nickname)
        data["direction_change_nicknames"] = nicknames
        write_macro_data(data)
    return True


def read_exchange_pixel_check_xy(data: dict) -> tuple[int, int] | None:
    value = data.get("exchange_pixel_check_xy", EXCHANGE_PIXEL_CHANGE_DEFAULT_XY)
    if value is None or value is False:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return int(value[0]), int(value[1])
    raise RuntimeError(f"invalid exchange_pixel_check_xy: {value!r}")


def read_exchange_check_pixel(img, xy: tuple[int, int] | None) -> tuple[int, int, int] | None:
    if xy is None:
        return None
    pixel = img.getpixel(xy)
    return tuple(int(value) for value in pixel[:3])


def read_blocked_turn_directions(data: dict) -> set[str]:
    raw_directions = data.get("blocked_turn_directions", [])
    if isinstance(raw_directions, str):
        directions = [raw_directions]
    elif isinstance(raw_directions, (list, tuple, set)):
        directions = raw_directions
    else:
        directions = []
    return {str(direction).strip() for direction in directions if str(direction).strip()}


def read_same_nickname_turn_seconds(data: dict) -> float:
    try:
        seconds = float(data.get("same_nickname_turn_seconds", SAME_NICKNAME_TURN_DEFAULT_SECONDS))
    except (TypeError, ValueError):
        seconds = SAME_NICKNAME_TURN_DEFAULT_SECONDS
    return max(0.0, seconds)


def read_config_float(data: dict, key: str, default: float) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        value = default
    return value


def read_config_bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def get_python_executable(data: dict) -> str:
    configured = str(data.get("python_executable") or "").strip()
    return configured or sys.executable


def build_role_command(role: str, args: argparse.Namespace, config: dict) -> list[str]:
    command = [
        get_python_executable(config),
        "-u",
        os.path.abspath(__file__),
        role,
    ]

    if args.status_interval is not None:
        command.extend(["--status-interval", str(args.status_interval)])
    if args.same_nickname_turn_seconds is not None:
        command.extend(["--same-nickname-turn-seconds", str(args.same_nickname_turn_seconds)])
    return command


def build_proxy_command(config: dict) -> list[str]:
    return [
        get_python_executable(config),
        "-u",
        os.path.join(BASE_DIR, "arduino_proxy.py"),
    ]


def stream_process_output(label: str, process: subprocess.Popen) -> None:
    if process.stdout is None:
        return

    for line in process.stdout:
        text = line.rstrip()
        if text:
            print(f"{label} | {text}", flush=True)


def launch_child_process(label: str, command: list[str], dry_run: bool) -> subprocess.Popen | None:
    display = subprocess.list2cmdline(command)
    if dry_run:
        print(f"[dry-run] {label}: {display}")
        return None

    print(f"[launcher] starting {label}: {display}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        command,
        cwd=BASE_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    threading.Thread(target=stream_process_output, args=(label, process), daemon=True).start()
    return process


def stop_child_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for label, process in processes:
        if process.poll() is None:
            print(f"[launcher] stopping {label}")
            process.terminate()

    deadline = time.time() + 5.0
    for label, process in processes:
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            print(f"[launcher] killing {label}")
            process.kill()


def is_process_alive(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def log_exited_processes(processes: dict[str, subprocess.Popen | None], reported: set[str]) -> None:
    for label, process in processes.items():
        if process is None or label in reported:
            continue
        code = process.poll()
        if code is not None:
            reported.add(label)
            print(f"[launcher] {label} exited with code {code}")


def start_independent_processes(
    args: argparse.Namespace,
    config: dict,
    processes: dict[str, subprocess.Popen | None],
) -> None:
    if is_process_alive(processes.get("server")) or is_process_alive(processes.get("client")):
        print("[independent] 이미 실행 중입니다.")
        return

    if read_config_bool(config, "start_arduino_proxy", False) or args.start_proxy:
        if not is_process_alive(processes.get("proxy")):
            processes["proxy"] = launch_child_process("proxy", build_proxy_command(config), False)
            time.sleep(1.0)

    processes["server"] = launch_child_process("server", build_role_command("server", args, config), False)
    start_delay = read_config_float(config, "start_delay_seconds", DUAL_START_DELAY_DEFAULT_SECONDS)
    time.sleep(max(0.0, start_delay))
    processes["client"] = launch_child_process("client", build_role_command("client", args, config), False)
    print("[independent] server/client 독립 매크로 시작")


def stop_independent_processes(processes: dict[str, subprocess.Popen | None]) -> None:
    targets = [
        (label, process)
        for label, process in processes.items()
        if label in {"server", "client"} and is_process_alive(process)
    ]
    if not targets:
        print("[independent] 실행 중인 server/client가 없습니다.")
        return

    stop_child_processes(targets)
    processes["server"] = None
    processes["client"] = None
    print("[independent] server/client 독립 매크로 중지")


def run_command_controller(args: argparse.Namespace, config: dict) -> int:
    if args.dry_run:
        launch_child_process("server", build_role_command("server", args, config), True)
        launch_child_process("client", build_role_command("client", args, config), True)
        return 0

    processes: dict[str, subprocess.Popen | None] = {
        "proxy": None,
        "server": None,
        "client": None,
    }
    reported_exits: set[str] = set()

    print("\n명령어: q=종료, 1=independent haste 시작, 2=independent haste 중지")
    try:
        while True:
            log_exited_processes(processes, reported_exits)
            cmd = input("> ").strip()
            if cmd == "q":
                stop_independent_processes(processes)
                proxy = processes.get("proxy")
                if is_process_alive(proxy):
                    stop_child_processes([("proxy", proxy)])
                break
            if cmd == "1":
                reported_exits.clear()
                start_independent_processes(args, config, processes)
            if cmd == "2":
                stop_independent_processes(processes)
    except KeyboardInterrupt:
        print("\n[independent] Ctrl+C 입력 - 종료합니다.")
        stop_independent_processes(processes)
        proxy = processes.get("proxy")
        if is_process_alive(proxy):
            stop_child_processes([("proxy", proxy)])
    return 0


@contextmanager
def input_lock():
    with open(INPUT_LOCK_PATH, "a+b") as lock_file:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def clear_chat_input() -> None:
    macro.arduino_key_down(win32con.VK_CONTROL)
    macro.arduino_key_press(win32con.VK_BACK)
    macro.arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)


def read_nickname_at_xy(check_xy: tuple[int, int]) -> str:
    x, y = check_xy
    with input_lock():
        macro.force_set_foreground_window(macro.lineage1_hwnd)
        macro.arduino_mouse_shift_click_right(x, y)
        time.sleep(0.15)
        img = macro.screenshot(hwnd=macro.lineage1_hwnd)
        nickname = macro.readInputText(img).strip()
        clear_chat_input()
    return nickname


def read_adena_after_exchange(adena_before: int | None, timeout: float = 6.0) -> int | None:
    deadline = time.time() + timeout
    last_value = None

    while True:
        with input_lock():
            value = macro.readAdena(max_attempts=3)
        if value is not None:
            last_value = value
            if adena_before is None or value > adena_before:
                return value

        if time.time() >= deadline:
            return last_value

        print(f"[solo] adena recheck: before={adena_before}, current={last_value}")
        time.sleep(0.5)


class IndependentHasteMacro:
    def __init__(self, role: str, status_interval: float, same_nickname_turn_seconds: float):
        self.role = role
        self.status_interval = status_interval
        self.same_nickname_turn_seconds = same_nickname_turn_seconds
        self.stage = "wait"
        self.base_shop_direction = macro.high_count_direction
        self.shop_direction = self.base_shop_direction
        self.was_low_mp = False
        self.direction_synced = False
        self.current_mp = 0
        self.current_available = 0
        self.last_potion_time = 0.0
        self.last_type_string_time = 0.0
        self.last_status_print_time = 0.0
        self.last_haste_check_time = 0.0
        self.last_return_check_time = 0.0
        self.last_pickup_time = 0.0
        self.greeted_nickname: str | None = None
        self.adena_before: int | None = None
        self.prev_brightness: float | None = None
        self.brightness_changed = False
        self.exchange_pixel_xy: tuple[int, int] | None = None
        self.exchange_pixel_before: tuple[int, int, int] | None = None
        self.exchange_pixel_changed = False
        self.same_nickname: str | None = None
        self.same_nickname_xy: tuple[int, int] | None = None
        self.same_nickname_since = 0.0
        self.exchange_window_nickname: str | None = None
        self.exchange_window_since = 0.0
        self.img = None

    def load_haste_config(self, direction: str) -> tuple[tuple[int, int], float, set[str]]:
        data = load_macro_data()
        xy = macro.get_configured_mouse_xy(direction=direction)

        interval = float(data.get("haste_check_interval_seconds", HASTE_CHECK_DEFAULT_INTERVAL))
        if interval <= 0:
            interval = HASTE_CHECK_DEFAULT_INTERVAL

        return xy, interval, read_direction_change_nicknames(data)

    def choose_shop_direction(
        self,
        preferred_direction: str | None,
        *,
        exclude_current: bool,
        label: str,
    ) -> str | None:
        data = load_macro_data()
        direction_change_nicknames = read_direction_change_nicknames(data)
        blocked_directions = read_blocked_turn_directions(data)
        excluded_directions = set(blocked_directions)
        if exclude_current:
            excluded_directions.add(self.shop_direction)

        candidates: list[str] = []
        if preferred_direction and preferred_direction in TURN_DIRECTIONS and preferred_direction not in excluded_directions:
            candidates.append(preferred_direction)

        fallback_candidates = [
            direction
            for direction in TURN_DIRECTIONS
            if direction not in excluded_directions and direction != preferred_direction
        ]
        random.shuffle(fallback_candidates)
        candidates.extend(fallback_candidates)

        if not candidates:
            print(f"[{self.role}] {label}: no direction candidates, excluded={sorted(excluded_directions)}")
            return None

        for direction in candidates:
            check_xy = macro.get_configured_mouse_xy(direction=direction)
            nickname = read_nickname_at_xy(check_xy)
            if nickname in direction_change_nicknames:
                print(f"[{self.role}] {label}: blocked by '{nickname}' at {check_xy} -> {direction}")
                continue

            print(f"[{self.role}] {label}: selected {direction}, nickname='{nickname}' at {check_xy}")
            return direction

        print(f"[{self.role}] {label}: every candidate blocked")
        return None

    def turn_to(self, direction: str, *, force: bool = False) -> bool:
        with input_lock():
            return macro.turn_to(direction, force=force)

    def type_string(self, text: str) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro.arduino_type_string(text)

    def key_press(self, vk: int) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro.key_press(vk)

    def press_f7(self) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro._arduino_send(f"KP,{win32con.VK_F7}")

    def cancel_exchange(self, label: str, chat_message: str | None = None) -> None:
        print(f"[{self.role}] {label} -> ESC")
        self.key_press(win32con.VK_ESCAPE)
        if chat_message:
            time.sleep(0.2)
            self.type_string(chat_message)
            self.last_type_string_time = time.time()
        self.reset_trade_state()
        time.sleep(0.5)

    def use_potion_if_needed(self) -> None:
        if self.current_available > LOW_MP_AVAILABLE_THRESHOLD:
            return
        now = time.time()
        if now - self.last_potion_time < POTION_COOLDOWN_SECONDS:
            return

        with input_lock():
            macro.use_potion()
        self.last_potion_time = now
        print(f"[{self.role}] potion used")

    def update_mp(self) -> None:
        self.img = macro.screenshot(hwnd=macro.lineage1_hwnd)
        mp = macro.readMp(self.img)
        if mp is None:
            with input_lock():
                macro.press_ctrl_a_for_mp_retry()
            print(f"[{self.role}] MP read failed; keeping previous MP={self.current_mp}")
            return

        self.current_mp = mp
        self.current_available = int(mp // 20)

    def print_status_if_due(self) -> None:
        if time.time() - self.last_status_print_time < self.status_interval:
            return

        status_xy = macro.get_configured_mouse_xy(direction=self.shop_direction)
        print(
            f"[{self.role}] direction={self.shop_direction}, "
            f"xy={status_xy}, MP={self.current_mp}, available={self.current_available}, "
            f"same_nickname_turn_seconds={self.same_nickname_turn_seconds:.1f}"
        )
        self.last_status_print_time = time.time()

    def reset_same_nickname_tracking(self) -> None:
        self.same_nickname = None
        self.same_nickname_xy = None
        self.same_nickname_since = 0.0

    def update_same_nickname_tracking(self, nickname: str, check_xy: tuple[int, int]) -> float:
        now = time.time()
        if not nickname:
            self.reset_same_nickname_tracking()
            return 0.0

        if self.same_nickname == nickname and self.same_nickname_xy == check_xy:
            return now - self.same_nickname_since

        self.same_nickname = nickname
        self.same_nickname_xy = check_xy
        self.same_nickname_since = now
        return 0.0

    def reset_exchange_window_tracking(self) -> None:
        self.exchange_window_nickname = None
        self.exchange_window_since = 0.0

    def update_exchange_window_tracking(self, nickname: str) -> float:
        now = time.time()
        if not nickname:
            self.reset_exchange_window_tracking()
            return 0.0

        if self.exchange_window_nickname == nickname:
            return now - self.exchange_window_since

        self.exchange_window_nickname = nickname
        self.exchange_window_since = now
        return 0.0

    def cancel_exchange_and_turn(self, label: str) -> None:
        print(f"[{self.role}] {label} -> ESC")
        self.key_press(win32con.VK_ESCAPE)
        time.sleep(0.2)
        direction = self.choose_shop_direction(
            None,
            exclude_current=True,
            label=label,
        )
        if direction is not None:
            self.shop_direction = direction
            self.last_return_check_time = time.time()
            self.reset_same_nickname_tracking()
            print(f"[{self.role}] direction changed -> {self.shop_direction}")
        else:
            print(f"[{self.role}] {label}; no alternate direction")
        self.reset_exchange_window_tracking()
        self.reset_trade_state()
        time.sleep(0.5)

    def handle_exchange_window_timeout(self, nickname: str) -> bool:
        if self.same_nickname_turn_seconds <= 0:
            return False

        elapsed = self.update_exchange_window_tracking(nickname)
        if elapsed < self.same_nickname_turn_seconds:
            return False

        if add_direction_change_nickname(nickname):
            print(f"[{self.role}] added direction_change_nicknames: '{nickname}'")

        self.cancel_exchange_and_turn(
            f"exchange window '{nickname}' for {elapsed:.1f}s"
        )
        return True

    def handle_low_mp(self) -> bool:
        should_face_low = self.current_available < macro.direction_threshold
        if not should_face_low:
            return False

        self.was_low_mp = True
        blocked_directions = read_blocked_turn_directions(load_macro_data())
        if macro.low_count_direction in blocked_directions:
            print(f"[{self.role}] low MP direction blocked -> {macro.low_count_direction}")
        elif self.turn_to(macro.low_count_direction):
            print(f"[{self.role}] low MP -> {macro.low_count_direction}")

        if time.time() - self.last_type_string_time >= 10:
            self.type_string("죄송합니다. 마나회복중입니다.")
            self.last_type_string_time = time.time()

        time.sleep(0.5)
        return True

    def recover_from_low_mp_if_needed(self) -> bool:
        if not self.was_low_mp:
            return True

        recover_direction = self.choose_shop_direction(
            macro.high_count_direction,
            exclude_current=False,
            label="MP recovered",
        )
        if recover_direction is None:
            time.sleep(0.5)
            return False

        self.shop_direction = recover_direction
        self.was_low_mp = False
        return True

    def try_haste_front_person(self, check_xy: tuple[int, int], direction_change_nicknames: set[str]) -> str | None:
        nickname = read_nickname_at_xy(check_xy)
        if not nickname:
            print(f"[{self.role}] no front nickname at {check_xy}")
            self.reset_same_nickname_tracking()
            return None

        if nickname in direction_change_nicknames:
            self.reset_same_nickname_tracking()
            direction = self.choose_shop_direction(
                None,
                exclude_current=True,
                label=f"blocked nickname '{nickname}'",
            )
            if direction is None:
                print(f"[{self.role}] blocked nickname '{nickname}' at {check_xy}; no alternate direction")
                return None
            print(f"[{self.role}] blocked nickname '{nickname}' at {check_xy}; switching to {direction}")
            return direction

        same_elapsed = self.update_same_nickname_tracking(nickname, check_xy)
        if self.same_nickname_turn_seconds > 0 and same_elapsed >= self.same_nickname_turn_seconds:
            if add_direction_change_nickname(nickname):
                print(f"[{self.role}] added direction_change_nicknames: '{nickname}'")

            direction = self.choose_shop_direction(
                None,
                exclude_current=True,
                label=f"same nickname '{nickname}' for {same_elapsed:.1f}s",
            )
            self.reset_same_nickname_tracking()
            if direction is None:
                print(
                    f"[{self.role}] same nickname '{nickname}' at {check_xy} "
                    f"for {same_elapsed:.1f}s; no alternate direction"
                )
                return None
            print(
                f"[{self.role}] same nickname '{nickname}' at {check_xy} "
                f"for {same_elapsed:.1f}s; switching to {direction}"
            )
            return direction

        print(f"[{self.role}] front nickname '{nickname}' at {check_xy} -> F7")
        self.press_f7()
        return "haste"

    def handle_wait_stage(self) -> None:
        if not self.direction_synced:
            self.turn_to(macro.current_direction, force=True)
            if self.turn_to(self.shop_direction):
                print(f"[{self.role}] shop direction={self.shop_direction}")
            self.direction_synced = True

        self.update_mp()
        self.use_potion_if_needed()
        self.print_status_if_due()

        if self.handle_low_mp():
            return

        if not self.recover_from_low_mp_if_needed():
            return

        if self.turn_to(self.shop_direction):
            print(f"[{self.role}] keep direction -> {self.shop_direction}")
            self.img = macro.screenshot(hwnd=macro.lineage1_hwnd)

        if time.time() - self.last_type_string_time >= 10:
            self.type_string(macro.get_adena_price_notice())
            self.last_type_string_time = time.time()

        haste_check_xy, haste_check_interval, direction_change_nicknames = self.load_haste_config(self.shop_direction)

        nickname = macro.readExchangeNickname(img=self.img)
        if nickname:
            if nickname in direction_change_nicknames:
                print(f"[{self.role}] blocked exchange nickname '{nickname}' -> ESC")
                self.key_press(win32con.VK_ESCAPE)
                self.reset_exchange_window_tracking()
                time.sleep(0.5)
                return

            self.greeted_nickname = nickname
            self.update_exchange_window_tracking(nickname)
            self.stage = "read_adena"
            return

        if (
            self.shop_direction != self.base_shop_direction
            and time.time() - self.last_return_check_time >= DIRECTION_RETURN_CHECK_INTERVAL
        ):
            self.last_return_check_time = time.time()
            check_xy = macro.get_configured_mouse_xy(direction=self.shop_direction)
            base_nickname = read_nickname_at_xy(check_xy)
            if base_nickname in direction_change_nicknames:
                print(f"[{self.role}] return check blocked by '{base_nickname}' at {check_xy}")
            else:
                print(f"[{self.role}] return check clear: nickname='{base_nickname}' at {check_xy}")
            time.sleep(0.5)
            return

        if time.time() - self.last_haste_check_time >= haste_check_interval:
            self.last_haste_check_time = time.time()
            haste_result = self.try_haste_front_person(haste_check_xy, direction_change_nicknames)
            if haste_result and haste_result != "haste":
                self.shop_direction = haste_result
                self.last_return_check_time = time.time()
                self.reset_same_nickname_tracking()
                print(f"[{self.role}] direction changed -> {self.shop_direction}")
            if haste_result:
                time.sleep(0.5)
                return

        time.sleep(0.5)

    def handle_read_adena_stage(self) -> None:
        self.img = macro.screenshot(hwnd=macro.lineage1_hwnd)
        exchange_nickname = macro.readExchangeNickname(img=self.img)
        if not exchange_nickname:
            self.reset_exchange_window_tracking()
            self.stage = "wait"
            return

        _, _, direction_change_nicknames = self.load_haste_config(self.shop_direction)
        if exchange_nickname in direction_change_nicknames:
            print(f"[{self.role}] blocked exchange request '{exchange_nickname}' -> ESC")
            self.key_press(win32con.VK_ESCAPE)
            self.reset_exchange_window_tracking()
            self.stage = "wait"
            time.sleep(0.5)
            return

        if self.handle_exchange_window_timeout(exchange_nickname):
            return

        with input_lock():
            self.adena_before = macro.readAdena()
        self.exchange_pixel_xy = read_exchange_pixel_check_xy(load_macro_data())
        self.exchange_pixel_before = read_exchange_check_pixel(self.img, self.exchange_pixel_xy)
        self.exchange_pixel_changed = False
        if self.exchange_pixel_xy is not None:
            print(
                f"[{self.role}] exchange check pixel baseline: "
                f"xy={self.exchange_pixel_xy}, rgb={self.exchange_pixel_before}"
            )
        self.press_f7()
        self.stage = "monitor_brightness"

    def handle_monitor_brightness_stage(self) -> None:
        self.img = macro.screenshot()
        exchange_nickname = macro.readExchangeNickname(self.img)
        if not exchange_nickname:
            self.reset_exchange_window_tracking()
            self.stage = "pickup"
            return

        if self.handle_exchange_window_timeout(exchange_nickname):
            return

        slot = macro.crop(self.img, 258, 677, 30, 30)
        brightness = macro.get_brightness(slot)
        print(f"[{self.role}] slot brightness={brightness:.2f}")

        if (
            self.exchange_pixel_xy is not None
            and self.exchange_pixel_before is not None
            and not self.exchange_pixel_changed
        ):
            current_pixel = read_exchange_check_pixel(self.img, self.exchange_pixel_xy)
            self.exchange_pixel_changed = current_pixel != self.exchange_pixel_before
            if self.exchange_pixel_changed:
                print(
                    f"[{self.role}] exchange check pixel changed: "
                    f"xy={self.exchange_pixel_xy}, {self.exchange_pixel_before} -> {current_pixel}"
                )

        if not self.brightness_changed and brightness > EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD:
            if self.exchange_pixel_xy is not None and not self.exchange_pixel_changed:
                self.cancel_exchange(
                    f"slot brightness {brightness:.2f} > {EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD:.1f}, "
                    "exchange check pixel unchanged",
                    chat_message="거래가 취소되었습니다",
                )
                return

            self.brightness_changed = True
            with input_lock():
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                macro.acceptExchange()
        self.prev_brightness = brightness
        time.sleep(0.5)

    def reset_trade_state(self) -> None:
        self.stage = "wait"
        self.greeted_nickname = None
        self.adena_before = None
        self.prev_brightness = None
        self.brightness_changed = False
        self.exchange_pixel_xy = None
        self.exchange_pixel_before = None
        self.exchange_pixel_changed = False
        self.reset_exchange_window_tracking()

    def handle_pickup_stage(self) -> None:
        adena_after = read_adena_after_exchange(self.adena_before)
        if self.adena_before is None or adena_after is None:
            print(f"[{self.role}] adena read failed: before={self.adena_before}, after={adena_after}")
            self.reset_trade_state()
            return

        received = adena_after - self.adena_before
        print(
            f"[{self.role}] adena changed: {self.adena_before} -> {adena_after} "
            f"(received={received}, slot_changed={self.brightness_changed})"
        )
        if received <= 0:
            self.reset_trade_state()
            return

        pickup_count = macro.get_pickup_count_for_adena(received)
        remaining = min(macro.direction_threshold, self.current_available, pickup_count)
        print(f"[{self.role}] pickup remaining={remaining}, available={self.current_available}")

        while remaining > 0:
            elapsed = time.time() - self.last_pickup_time
            if elapsed < SAME_PICKUP_DELAY_SECONDS:
                time.sleep(SAME_PICKUP_DELAY_SECONDS - elapsed)

            with input_lock():
                macro.pickup_lineage1(
                    target_nickname=self.greeted_nickname,
                    direction=self.shop_direction,
                )
            self.last_pickup_time = time.time()
            self.current_available -= 1
            remaining -= 1

        if received > 0:
            self.type_string("감사합니다!")
            self.last_type_string_time = time.time()
            time.sleep(2.5)

        self.reset_trade_state()

    def run(self) -> None:
        while True:
            if self.stage == "wait":
                self.handle_wait_stage()
            elif self.stage == "read_adena":
                self.handle_read_adena_stage()
            elif self.stage == "monitor_brightness":
                self.handle_monitor_brightness_stage()
            elif self.stage == "pickup":
                self.handle_pickup_stage()
            else:
                raise RuntimeError(f"unknown stage: {self.stage}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run independent haste. Without a role, this opens a q/1/2 controller."
        )
    )
    parser.add_argument(
        "role",
        nargs="?",
        choices=["server", "client"],
        help="Window role to run independently. Omit this to use the controller.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=None,
        help="Seconds between status log lines.",
    )
    parser.add_argument(
        "--same-nickname-turn-seconds",
        type=float,
        default=None,
        help=(
            "Turn to another direction if the same nickname is detected at the same "
            "check coordinate for this many seconds. Uses independent_haste_config.json "
            "same_nickname_turn_seconds when omitted. 0 disables it."
        ),
    )
    parser.add_argument(
        "--start-proxy",
        action="store_true",
        help="Also start arduino_proxy.py before server/client when launching both.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print server/client launch commands without starting them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_independent_haste_config()

    if args.role is None:
        return run_command_controller(args, config)

    if args.dry_run:
        print("--dry-run is only used when launching both server and client.")
        return 0

    if args.start_proxy:
        print("--start-proxy is only used when launching both server and client.")

    if args.status_interval is None:
        status_interval = read_config_float(config, "status_interval", STATUS_INTERVAL_DEFAULT_SECONDS)
    else:
        status_interval = args.status_interval
    status_interval = max(0.5, status_interval)

    if args.same_nickname_turn_seconds is None:
        same_nickname_turn_seconds = read_same_nickname_turn_seconds(config)
    else:
        same_nickname_turn_seconds = max(0.0, args.same_nickname_turn_seconds)

    with input_lock():
        macro.init_setting(args.role)

    app = IndependentHasteMacro(
        args.role,
        status_interval=status_interval,
        same_nickname_turn_seconds=same_nickname_turn_seconds,
    )
    print(f"[{args.role}] standalone haste macro started. Press Ctrl+C to stop.")
    try:
        app.run()
    except KeyboardInterrupt:
        print(f"\n[{args.role}] stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
