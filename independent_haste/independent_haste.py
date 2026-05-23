import argparse
from contextlib import contextmanager
import errno
import json
import msvcrt
import os
import random
import subprocess
import sys
import threading
import time

import win32api
import win32con

import macro


# 이 파일은 독립 헤이스트 매크로의 메인 실행 파일입니다.
# 인자 없이 실행하면 q/1/2 명령 컨트롤러가 열리고,
# 내부적으로 server/client 역할 프로세스를 각각 띄워 독립적으로 돌립니다.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DATA_PATH = os.path.join(BASE_DIR, "macro_data.json")
INDEPENDENT_HASTE_CONFIG_PATH = os.path.join(BASE_DIR, "independent_haste_config.json")
INPUT_LOCK_PATH = os.path.join(BASE_DIR, ".independent_haste.lock")
F12_STOP_PATH = os.path.join(BASE_DIR, ".f12_stop")
CHILD_PROCESS_ENV = "INDEPENDENT_HASTE_CHILD"
_f12_stop_reported = False

# 자주 바꾸는 운영 기준값입니다. 좌표/방향/가격은 macro_data.json에서 관리합니다.
# 헤이스트 가능 횟수(current_available)가 이 값 이하이면 MP 포션(F8)을 사용합니다.
LOW_MP_AVAILABLE_THRESHOLD = 4

# MP 포션을 한 번 사용한 뒤 다시 사용할 수 있을 때까지 기다리는 시간(초)입니다.
POTION_COOLDOWN_SECONDS = 600.0

# macro_data.json에 haste_check_interval_seconds가 없거나 잘못됐을 때 쓰는 기본 검사 간격(초)입니다.
HASTE_CHECK_DEFAULT_INTERVAL = 3.0

# 기본 장사 방향이 아닌 방향에 있을 때 자리 확인을 다시 시도하는 간격(초)입니다.
DIRECTION_RETURN_CHECK_INTERVAL = 30.0

# 장사 가능 상태에서 실제 방향 클릭이 씹힌 경우를 보정하려고 같은 장사 방향을 다시 누르는 간격(초)입니다.
SHOP_DIRECTION_FORCE_INTERVAL_SECONDS = 15.0

# 교환창 슬롯 영역 평균 밝기가 이 값보다 크면 교환 OK 후보로 판단합니다.
EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD = 120.0

# 픽업을 연속으로 여러 번 할 때 같은 창에서 다음 픽업까지 기다리는 최소 시간(초)입니다.
SAME_PICKUP_DELAY_SECONDS = 1.0

# independent_haste_config.json에 same_nickname_turn_seconds가 없을 때의 기본값입니다. 0이면 비활성화입니다.
SAME_NICKNAME_TURN_DEFAULT_SECONDS = 0.0
NO_FRONT_NICKNAME_TURN_DEFAULT_SECONDS = 35.0

# 상태 로그를 몇 초마다 출력할지 정하는 기본값입니다.
STATUS_INTERVAL_DEFAULT_SECONDS = 3.0

# MP 회복중일 때 안내 채팅을 다시 입력하는 기본 간격(초)입니다.
LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS = 10.0

# macro_data.json에 low_mp_messages가 없거나 비어 있을 때 쓰는 기본 안내 문구입니다.
LOW_MP_MESSAGES_DEFAULT = (
    "죄송합니다. 마나회복중입니다.",
    "잠시만 기다려주세요. 마나회복중입니다.",
    "마나 회복 후 바로 진행하겠습니다.",
)

# q/1/2 컨트롤러에서 server 실행 후 client를 띄우기 전 기다리는 기본 시간(초)입니다.
DUAL_START_DELAY_DEFAULT_SECONDS = 2.0

# server/client가 동시에 입력하려 할 때 파일 락을 다시 잡아보는 간격(초)입니다.
INPUT_LOCK_RETRY_INTERVAL_SECONDS = 0.05

# 방향전환 후보로 사용하는 8방향 이름입니다. macro_data.json의 방향별 좌표 키와 맞아야 합니다.
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


def clear_f12_stop_request() -> None:
    try:
        os.remove(F12_STOP_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"[f12] failed to clear stop request: {exc}")


def _write_f12_stop_request() -> None:
    if os.path.exists(F12_STOP_PATH):
        return
    try:
        with open(F12_STOP_PATH, "w", encoding="ascii") as f:
            f.write(str(time.time()))
    except OSError as exc:
        print(f"[f12] failed to write stop request: {exc}")


def is_f12_stop_requested() -> bool:
    state = win32api.GetAsyncKeyState(win32con.VK_F12)
    if state & 0x8000 or state & 0x0001:
        _write_f12_stop_request()
        return True
    return os.path.exists(F12_STOP_PATH)


def request_f12_stop(label: str) -> bool:
    global _f12_stop_reported
    if not is_f12_stop_requested():
        return False
    if not _f12_stop_reported:
        print(f"[{label}] F12 stop")
        _f12_stop_reported = True
    return True


def sleep_interruptible(seconds: float, label: str = "macro") -> bool:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if request_f12_stop(label):
            return True
        time.sleep(min(0.05, deadline - time.time()))
    return False


def load_macro_data() -> dict:
    """좌표, 방향, 가격, 차단 닉네임 같은 매크로 공통 설정을 읽습니다."""
    with open(MACRO_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_macro_data(data: dict) -> None:
    """자동 추가된 direction_change_nicknames를 안전하게 파일에 저장합니다."""
    tmp_path = f"{MACRO_DATA_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(tmp_path, MACRO_DATA_PATH)


def load_independent_haste_config() -> dict:
    """독립 실행 전용 설정을 읽습니다. 파일이 없거나 깨졌으면 기본값을 씁니다."""
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
    """닉네임 설정을 중복 없는 문자열 리스트로 정리합니다."""
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
    """오래 서 있던 닉네임을 direction_change_nicknames에 즉시 추가합니다."""
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


def read_blocked_turn_directions(data: dict) -> set[str]:
    raw_directions = data.get("blocked_turn_directions", [])
    if isinstance(raw_directions, str):
        directions = [raw_directions]
    elif isinstance(raw_directions, (list, tuple, set)):
        directions = raw_directions
    else:
        directions = []
    return {str(direction).strip() for direction in directions if str(direction).strip()}


def read_low_mp_message_config(data: dict) -> tuple[float, list[str]]:
    try:
        interval = float(
            data.get("low_mp_message_interval_seconds", LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS)
        )
    except (TypeError, ValueError):
        interval = LOW_MP_MESSAGE_DEFAULT_INTERVAL_SECONDS

    raw_messages = data.get("low_mp_messages", LOW_MP_MESSAGES_DEFAULT)
    if isinstance(raw_messages, str):
        messages = [raw_messages.strip()] if raw_messages.strip() else []
    elif isinstance(raw_messages, (list, tuple)):
        messages = [str(message).strip() for message in raw_messages if str(message).strip()]
    else:
        messages = []

    if not messages:
        messages = list(LOW_MP_MESSAGES_DEFAULT)

    return max(0.0, interval), messages


def read_same_nickname_turn_seconds(data: dict) -> float:
    try:
        seconds = float(data.get("same_nickname_turn_seconds", SAME_NICKNAME_TURN_DEFAULT_SECONDS))
    except (TypeError, ValueError):
        seconds = SAME_NICKNAME_TURN_DEFAULT_SECONDS
    return max(0.0, seconds)


def read_no_front_nickname_turn_seconds(data: dict) -> float:
    try:
        seconds = float(
            data.get("no_front_nickname_turn_seconds", NO_FRONT_NICKNAME_TURN_DEFAULT_SECONDS)
        )
    except (TypeError, ValueError):
        seconds = NO_FRONT_NICKNAME_TURN_DEFAULT_SECONDS
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
    """컨트롤러가 server/client 자식 프로세스를 띄울 때 쓰는 명령을 만듭니다."""
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
    if args.no_front_nickname_turn_seconds is not None:
        command.extend(["--no-front-nickname-turn-seconds", str(args.no_front_nickname_turn_seconds)])
    return command


def build_proxy_command(config: dict) -> list[str]:
    return [
        get_python_executable(config),
        "-u",
        os.path.join(BASE_DIR, "arduino_proxy.py"),
    ]


def stream_process_output(label: str, process: subprocess.Popen) -> None:
    """server/client 자식 프로세스 로그를 현재 콘솔에 prefix를 붙여 출력합니다."""
    if process.stdout is None:
        return

    for line in process.stdout:
        text = line.rstrip()
        if text:
            print(f"{label} | {text}", flush=True)


def launch_child_process(label: str, command: list[str], dry_run: bool) -> subprocess.Popen | None:
    """새 콘솔을 만들지 않고 자식 프로세스를 실행해서 로그를 현재 콘솔로 모읍니다."""
    display = subprocess.list2cmdline(command)
    if dry_run:
        print(f"[dry-run] {label}: {display}")
        return None

    print(f"[launcher] starting {label}: {display}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env[CHILD_PROCESS_ENV] = "1"
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
    """q/2/Ctrl+C 입력 시 server/client/proxy 프로세스를 정리합니다."""
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
    """명령어 1 입력 시 server/client 독립 매크로를 시작합니다."""
    if is_process_alive(processes.get("server")) or is_process_alive(processes.get("client")):
        print("[independent] 이미 실행 중입니다.")
        return

    clear_f12_stop_request()

    if read_config_bool(config, "start_arduino_proxy", False) or args.start_proxy:
        if not is_process_alive(processes.get("proxy")):
            processes["proxy"] = launch_child_process("proxy", build_proxy_command(config), False)
            if sleep_interruptible(1.0, "launcher"):
                return

    processes["server"] = launch_child_process("server", build_role_command("server", args, config), False)
    start_delay = read_config_float(config, "start_delay_seconds", DUAL_START_DELAY_DEFAULT_SECONDS)
    if sleep_interruptible(max(0.0, start_delay), "launcher"):
        stop_independent_processes(processes)
        return
    processes["client"] = launch_child_process("client", build_role_command("client", args, config), False)
    print("[independent] server/client 독립 매크로 시작")


def stop_independent_processes(processes: dict[str, subprocess.Popen | None]) -> None:
    """명령어 2 입력 시 server/client 독립 매크로만 중지합니다."""
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
    """기존 server.py처럼 q/1/2 명령으로 독립 매크로를 제어하는 진입점입니다."""
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
    """두 프로세스가 동시에 마우스/키보드/파일 입력을 건드리지 않게 잠급니다."""
    with open(INPUT_LOCK_PATH, "a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()

        acquired = False
        while not acquired:
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                time.sleep(INPUT_LOCK_RETRY_INTERVAL_SECONDS)
        try:
            yield
        finally:
            if acquired:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def clear_chat_input() -> None:
    """우클릭 닉네임 확인 후 채팅 입력칸에 남은 글자를 지웁니다."""
    macro.arduino_key_down(win32con.VK_CONTROL)
    macro.arduino_key_press(win32con.VK_BACK)
    macro.arduino_key_up(win32con.VK_CONTROL)
    time.sleep(0.1)


def read_nickname_at_xy(check_xy: tuple[int, int]) -> str:
    """지정 좌표를 우클릭해서 입력창에 잡힌 닉네임을 OCR로 읽습니다."""
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
    """교환 완료 후 아데나가 실제로 증가했는지 여러 번 재확인합니다."""
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
        if sleep_interruptible(0.5, "solo"):
            return last_value


class IndependentHasteMacro:
    """server 또는 client 한 창을 독립적으로 담당하는 상태 머신입니다."""

    def __init__(
        self,
        role: str,
        status_interval: float,
        same_nickname_turn_seconds: float,
        no_front_nickname_turn_seconds: float,
    ):
        self.role = role
        self.status_interval = status_interval
        self.same_nickname_turn_seconds = same_nickname_turn_seconds
        self.no_front_nickname_turn_seconds = no_front_nickname_turn_seconds
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
        self.last_shop_direction_force_time = time.time()
        self.last_pickup_time = 0.0
        self.low_mp_message_index = 0
        self.greeted_nickname: str | None = None
        self.adena_before: int | None = None
        self.prev_brightness: float | None = None
        self.brightness_changed = False
        self.same_nickname: str | None = None
        self.same_nickname_xy: tuple[int, int] | None = None
        self.same_nickname_since = 0.0
        self.no_front_nickname_xy: tuple[int, int] | None = None
        self.no_front_nickname_since = 0.0
        self.exchange_window_nickname: str | None = None
        self.exchange_window_since = 0.0
        self.img = None

    def load_haste_config(self, direction: str) -> tuple[tuple[int, int], float, set[str]]:
        """현재 방향 기준 확인 좌표와 차단 닉네임 목록을 최신 설정에서 읽습니다."""
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
        """방향전환 후보를 만들고, 각 방향 좌표에 차단 닉네임이 없는 곳을 고릅니다."""
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
        """실제 캐릭터 방향전환은 macro.py에 맡기고 입력 충돌만 여기서 막습니다."""
        with input_lock():
            return macro.turn_to(direction, force=force)

    def type_string(self, text: str) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro.arduino_type_string(text)

    def type_next_low_mp_message(self, *, force: bool = False) -> bool:
        interval, messages = read_low_mp_message_config(load_macro_data())
        now = time.time()
        if not force and now - self.last_type_string_time < interval:
            return False

        message = messages[self.low_mp_message_index % len(messages)]
        self.low_mp_message_index += 1
        self.type_string(message)
        self.last_type_string_time = time.time()
        return True

    def key_press(self, vk: int) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro.key_press(vk)

    def press_f7(self) -> None:
        with input_lock():
            macro.force_set_foreground_window(macro.lineage1_hwnd)
            macro._arduino_send(f"KP,{win32con.VK_F7}")

    def use_potion_if_needed(self) -> None:
        """헤이스트 가능 횟수가 낮으면 F8 포션을 사용합니다. 쿨타임은 10분입니다."""
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
        """현재 창 스크린샷에서 MP를 읽고, MP/20으로 가능한 헤이스트 횟수를 계산합니다."""
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
            f"same_nickname_turn_seconds={self.same_nickname_turn_seconds:.1f}, "
            f"no_front_nickname_turn_seconds={self.no_front_nickname_turn_seconds:.1f}"
        )
        self.last_status_print_time = time.time()

    def reset_same_nickname_tracking(self) -> None:
        self.same_nickname = None
        self.same_nickname_xy = None
        self.same_nickname_since = 0.0

    def update_same_nickname_tracking(self, nickname: str, check_xy: tuple[int, int]) -> float:
        """같은 좌표에 같은 닉네임이 얼마나 오래 유지되는지 누적합니다."""
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

    def reset_no_front_nickname_tracking(self) -> None:
        self.no_front_nickname_xy = None
        self.no_front_nickname_since = 0.0

    def update_no_front_nickname_tracking(self, check_xy: tuple[int, int]) -> float:
        now = time.time()
        if self.no_front_nickname_xy == check_xy:
            return now - self.no_front_nickname_since

        self.no_front_nickname_xy = check_xy
        self.no_front_nickname_since = now
        return 0.0

    def reset_exchange_window_tracking(self) -> None:
        self.exchange_window_nickname = None
        self.exchange_window_since = 0.0

    def update_exchange_window_tracking(self, nickname: str) -> float:
        """같은 거래창 닉네임이 얼마나 오래 열려 있는지 누적합니다."""
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
        """문제 있는 거래창을 닫고 다른 장사 방향을 찾습니다."""
        print(f"[{self.role}] {label} -> ESC")
        self.key_press(win32con.VK_ESCAPE)
        if sleep_interruptible(0.2, self.role):
            return
        direction = self.choose_shop_direction(
            None,
            exclude_current=True,
            label=label,
        )
        if direction is not None:
            self.shop_direction = direction
            self.last_return_check_time = time.time()
            self.last_shop_direction_force_time = time.time()
            self.reset_same_nickname_tracking()
            self.reset_no_front_nickname_tracking()
            print(f"[{self.role}] direction changed -> {self.shop_direction}")
        else:
            print(f"[{self.role}] {label}; no alternate direction")
        self.reset_exchange_window_tracking()
        self.reset_trade_state()
        sleep_interruptible(0.5, self.role)

    def handle_exchange_window_timeout(self, nickname: str) -> bool:
        """거래창이 오래 열려 있으면 닉네임을 자동 차단 목록에 넣고 방향전환합니다."""
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
        """MP가 부족하면 low_count_direction으로 돌고 마나회복 안내를 출력합니다."""
        should_face_low = self.current_available < macro.direction_threshold
        if not should_face_low:
            return False

        entering_low_mp = not self.was_low_mp
        self.was_low_mp = True
        sent_low_mp_message = False
        blocked_directions = read_blocked_turn_directions(load_macro_data())
        if macro.low_count_direction in blocked_directions:
            print(f"[{self.role}] low MP direction blocked -> {macro.low_count_direction}")
        elif self.turn_to(macro.low_count_direction):
            print(f"[{self.role}] low MP -> {macro.low_count_direction}")

        if entering_low_mp:
            sent_low_mp_message = self.type_next_low_mp_message(force=True)
        if not sent_low_mp_message:
            self.type_next_low_mp_message()

        sleep_interruptible(0.5, self.role)
        return True

    def recover_from_low_mp_if_needed(self) -> bool:
        """MP 부족 상태에서 회복되면 high_count_direction 우선으로 장사 방향을 다시 잡습니다."""
        if not self.was_low_mp:
            return True

        recover_direction = self.choose_shop_direction(
            macro.high_count_direction,
            exclude_current=False,
            label="MP recovered",
        )
        if recover_direction is None:
            sleep_interruptible(0.5, self.role)
            return False

        self.shop_direction = recover_direction
        self.was_low_mp = False
        self.last_shop_direction_force_time = time.time()
        self.reset_same_nickname_tracking()
        self.reset_no_front_nickname_tracking()
        return True

    def try_return_to_base_direction(self, direction_change_nicknames: set[str]) -> bool:
        """Check high_count_direction and return to it when it is available."""
        blocked_directions = read_blocked_turn_directions(load_macro_data())
        if self.base_shop_direction in blocked_directions:
            print(f"[{self.role}] return check blocked direction -> {self.base_shop_direction}")
            return False

        check_xy = macro.get_configured_mouse_xy(direction=self.base_shop_direction)
        base_nickname = read_nickname_at_xy(check_xy)
        if base_nickname in direction_change_nicknames:
            print(
                f"[{self.role}] return check blocked by '{base_nickname}' "
                f"at {check_xy} -> {self.base_shop_direction}"
            )
            return False

        self.shop_direction = self.base_shop_direction
        self.last_shop_direction_force_time = time.time()
        self.reset_same_nickname_tracking()
        self.reset_no_front_nickname_tracking()
        print(
            f"[{self.role}] return check clear: nickname='{base_nickname}' "
            f"at {check_xy}; direction changed -> {self.shop_direction}"
        )
        return True

    def try_haste_front_person(self, check_xy: tuple[int, int], direction_change_nicknames: set[str]) -> str | None:
        """앞 사람 닉네임을 읽고, 차단/장기정체/정상 헤이스트 여부를 판단합니다."""
        nickname = read_nickname_at_xy(check_xy)
        if not nickname:
            print(f"[{self.role}] no front nickname at {check_xy}")
            self.reset_same_nickname_tracking()
            no_front_elapsed = self.update_no_front_nickname_tracking(check_xy)
            if (
                self.no_front_nickname_turn_seconds > 0
                and no_front_elapsed >= self.no_front_nickname_turn_seconds
            ):
                direction = self.choose_shop_direction(
                    None,
                    exclude_current=True,
                    label=f"no front nickname for {no_front_elapsed:.1f}s",
                )
                self.reset_no_front_nickname_tracking()
                if direction is None:
                    print(
                        f"[{self.role}] no front nickname at {check_xy} "
                        f"for {no_front_elapsed:.1f}s; no alternate direction"
                    )
                    return None
                print(
                    f"[{self.role}] no front nickname at {check_xy} "
                    f"for {no_front_elapsed:.1f}s; switching to {direction}"
                )
                return direction
            return None

        self.reset_no_front_nickname_tracking()
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
        """기본 대기 단계: MP 확인, 방향 유지, 광고, 거래창/앞사람 확인을 처리합니다."""
        if not self.direction_synced:
            self.turn_to(macro.current_direction, force=True)
            if self.turn_to(self.shop_direction):
                print(f"[{self.role}] shop direction={self.shop_direction}")
            self.last_shop_direction_force_time = time.time()
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
            self.reset_no_front_nickname_tracking()
            if nickname in direction_change_nicknames:
                print(f"[{self.role}] blocked exchange nickname '{nickname}' -> ESC")
                self.key_press(win32con.VK_ESCAPE)
                self.reset_exchange_window_tracking()
                sleep_interruptible(0.5, self.role)
                return

            self.greeted_nickname = nickname
            self.update_exchange_window_tracking(nickname)
            self.stage = "read_adena"
            return

        if time.time() - self.last_shop_direction_force_time >= SHOP_DIRECTION_FORCE_INTERVAL_SECONDS:
            if self.turn_to(self.shop_direction, force=True):
                print(f"[{self.role}] force shop direction -> {self.shop_direction}")
                self.img = macro.screenshot(hwnd=macro.lineage1_hwnd)
            self.last_shop_direction_force_time = time.time()
            sleep_interruptible(0.2, self.role)
            return

        if (
            self.shop_direction != self.base_shop_direction
            and time.time() - self.last_return_check_time >= DIRECTION_RETURN_CHECK_INTERVAL
        ):
            self.last_return_check_time = time.time()
            self.try_return_to_base_direction(direction_change_nicknames)
            sleep_interruptible(0.5, self.role)
            return

        if time.time() - self.last_haste_check_time >= haste_check_interval:
            self.last_haste_check_time = time.time()
            haste_result = self.try_haste_front_person(haste_check_xy, direction_change_nicknames)
            if haste_result and haste_result != "haste":
                self.shop_direction = haste_result
                self.last_return_check_time = time.time()
                self.last_shop_direction_force_time = time.time()
                self.reset_same_nickname_tracking()
                self.reset_no_front_nickname_tracking()
                print(f"[{self.role}] direction changed -> {self.shop_direction}")
            if haste_result:
                sleep_interruptible(0.5, self.role)
                return

        sleep_interruptible(0.5, self.role)

    def handle_read_adena_stage(self) -> None:
        """거래 시작 직전 아데나와 픽셀 기준값을 저장하고 F7로 수락 준비를 합니다."""
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
            sleep_interruptible(0.5, self.role)
            return

        if self.handle_exchange_window_timeout(exchange_nickname):
            return

        with input_lock():
            self.adena_before = macro.readAdena()
        self.press_f7()
        self.stage = "monitor_brightness"

    def handle_monitor_brightness_stage(self) -> None:
        """픽셀 변화와 슬롯 밝기를 함께 확인해 교환 OK 또는 ESC 취소를 결정합니다."""
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

        if not self.brightness_changed and brightness > EXCHANGE_SLOT_BRIGHTNESS_THRESHOLD:
            self.brightness_changed = True
            with input_lock():
                macro.force_set_foreground_window(macro.lineage1_hwnd)
                macro.acceptExchange()
        self.prev_brightness = brightness
        sleep_interruptible(0.5, self.role)

    def reset_trade_state(self) -> None:
        """한 번의 거래 흐름이 끝났거나 취소됐을 때 임시 상태를 초기화합니다."""
        self.stage = "wait"
        self.greeted_nickname = None
        self.adena_before = None
        self.prev_brightness = None
        self.brightness_changed = False
        self.reset_exchange_window_tracking()

    def handle_pickup_stage(self) -> None:
        """받은 아데나를 방 수로 계산하고 자기 창 기준으로 픽업을 수행합니다."""
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
            self.type_string("아데나를 받지 못 했습니다. 다시 부탁드리겠습니다.")
            self.last_type_string_time = time.time()
            sleep_interruptible(1.0, self.role)
            self.reset_trade_state()
            return

        if received < macro.adena_per_pickup:
            print(
                f"[{self.role}] adena too low: "
                f"received={received}, required={macro.adena_per_pickup}"
            )
            self.type_string(f"아데나가 부족합니다. 1방 {macro.adena_per_pickup}원입니다.")
            self.last_type_string_time = time.time()
            sleep_interruptible(1.0, self.role)
            self.reset_trade_state()
            return

        pickup_count = macro.get_pickup_count_for_adena(received)
        remaining = min(macro.direction_threshold, self.current_available, pickup_count)
        successful_pickups = 0
        print(f"[{self.role}] pickup remaining={remaining}, available={self.current_available}")

        while remaining > 0 and not request_f12_stop(self.role):
            elapsed = time.time() - self.last_pickup_time
            if elapsed < SAME_PICKUP_DELAY_SECONDS:
                if sleep_interruptible(SAME_PICKUP_DELAY_SECONDS - elapsed, self.role):
                    break

            with input_lock():
                pickup_ok = macro.pickup_lineage1(
                    target_nickname=self.greeted_nickname,
                    direction=self.shop_direction,
                )
            if not pickup_ok:
                print(f"[{self.role}] target check failed -> skip current pickup")
                self.last_type_string_time = time.time()
            else:
                self.current_available -= 1
                successful_pickups += 1

            self.last_pickup_time = time.time()
            remaining -= 1

        if successful_pickups > 0:
            self.type_string("감사합니다!")
            self.last_type_string_time = time.time()
            sleep_interruptible(2.5, self.role)

        self.reset_trade_state()

    def run(self) -> None:
        """현재 stage 값에 맞는 처리 함수를 계속 호출합니다."""
        while not request_f12_stop(self.role):
            try:
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
            except macro.RestartButtonClicked:
                print(f"[{self.role}] Restart clicked - independent macro stopped")
                break


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
        "--no-front-nickname-turn-seconds",
        type=float,
        default=None,
        help=(
            "Turn to another direction if no front nickname is detected for this many "
            "seconds. Uses independent_haste_config.json no_front_nickname_turn_seconds "
            "when omitted. 0 disables it."
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

    if os.environ.get(CHILD_PROCESS_ENV) != "1":
        clear_f12_stop_request()

    if args.status_interval is None:
        status_interval = read_config_float(config, "status_interval", STATUS_INTERVAL_DEFAULT_SECONDS)
    else:
        status_interval = args.status_interval
    status_interval = max(0.5, status_interval)

    if args.same_nickname_turn_seconds is None:
        same_nickname_turn_seconds = read_same_nickname_turn_seconds(config)
    else:
        same_nickname_turn_seconds = max(0.0, args.same_nickname_turn_seconds)

    if args.no_front_nickname_turn_seconds is None:
        no_front_nickname_turn_seconds = read_no_front_nickname_turn_seconds(config)
    else:
        no_front_nickname_turn_seconds = max(0.0, args.no_front_nickname_turn_seconds)

    with input_lock():
        macro.init_setting(args.role)

    app = IndependentHasteMacro(
        args.role,
        status_interval=status_interval,
        same_nickname_turn_seconds=same_nickname_turn_seconds,
        no_front_nickname_turn_seconds=no_front_nickname_turn_seconds,
    )
    print(f"[{args.role}] standalone haste macro started. Press F12 or Ctrl+C to stop.")
    try:
        app.run()
    except KeyboardInterrupt:
        print(f"\n[{args.role}] stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
