from __future__ import annotations

import argparse
import json
import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(BASE_DIR), "macro_data.json")
DIRECTIONS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)


def _config_key(direction: str) -> str:
    return f"turn_{direction}_xy"


def _normalize_direction(value: str) -> str:
    direction = value.strip().lower()
    if direction.startswith("turn_") and direction.endswith("_xy"):
        direction = direction[5:-3]
    if direction not in DIRECTIONS:
        choices = ", ".join(DIRECTIONS)
        raise RuntimeError(f"unknown direction: {value} (choose from: {choices})")
    return direction


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_config(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=4)
        f.write("\n")


def _read_turn_positions(path: str) -> dict[str, list[int]]:
    data = _load_config(path)
    positions: dict[str, list[int]] = {}
    for direction in DIRECTIONS:
        value = data.get(_config_key(direction), [0, 0])
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise RuntimeError(f"invalid coordinate for {_config_key(direction)}: {value!r}")
        positions[direction] = [int(value[0]), int(value[1])]
    return positions


def show_turn_positions(path: str) -> None:
    positions = _read_turn_positions(path)
    print(f"[turn] config={path}")
    for direction in DIRECTIONS:
        x, y = positions[direction]
        print(f"[turn] {direction:<10} {x} {y}")


def _parse_updates(parts: list[str]) -> list[tuple[str, int, int]]:
    if len(parts) % 3 != 0:
        raise RuntimeError("direction updates require sets of <direction> <x> <y>")

    updates: list[tuple[str, int, int]] = []
    for idx in range(0, len(parts), 3):
        normalized = _normalize_direction(parts[idx])
        x = int(parts[idx + 1])
        y = int(parts[idx + 2])
        updates.append((normalized, x, y))
    return updates


def set_turn_positions(path: str, updates: list[tuple[str, int, int]]) -> None:
    data = _load_config(path)
    for direction, x, y in updates:
        data[_config_key(direction)] = [int(x), int(y)]
    _save_config(path, data)
    for direction, x, y in updates:
        print(f"[turn] saved {direction}: [{int(x)}, {int(y)}]")


def print_help() -> None:
    print("Commands")
    print("  show")
    print("  north <x> <y>")
    print("  north <x> <y> northeast <x> <y> ...")
    print("  northeast <x> <y>")
    print("  east <x> <y>")
    print("  southeast <x> <y>")
    print("  south <x> <y>")
    print("  southwest <x> <y>")
    print("  west <x> <y>")
    print("  northwest <x> <y>")
    print("  quit")


def run_shell(config_path: str) -> int:
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
            command = parts[0].lower()
            if command in {"quit", "exit"}:
                return 0
            if command == "help":
                print_help()
            elif command == "show":
                show_turn_positions(config_path)
            elif len(parts) >= 3 and len(parts) % 3 == 0:
                set_turn_positions(config_path, _parse_updates(parts))
            else:
                print("[error] unknown command")
                print_help()
        except Exception as exc:
            print(f"[error] {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set macro turn coordinates by direction name."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to macro_data.json.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show all saved direction coordinates and exit.",
    )
    parser.add_argument(
        "pairs",
        nargs="*",
        help="Direction coordinate triplets, for example 'north 654 292'.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.show:
        show_turn_positions(args.config)
        return 0

    if not args.pairs:
        return run_shell(args.config)

    if len(args.pairs) == 1 and args.pairs[0].lower() == "show":
        show_turn_positions(args.config)
        return 0

    set_turn_positions(args.config, _parse_updates(args.pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
