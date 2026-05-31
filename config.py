import json
import os
from copy import deepcopy
from typing import Any


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_data.json")

DEFAULTS: dict[str, Any] = {
    "logging": {
        "enabled": True,
    },
}

_config: dict[str, Any] | None = None


def _merge_missing(data: dict[str, Any], defaults: dict[str, Any]) -> bool:
    changed = False
    for key, value in defaults.items():
        if key not in data:
            data[key] = deepcopy(value)
            changed = True
            continue
        if isinstance(data[key], dict) and isinstance(value, dict):
            changed = _merge_missing(data[key], value) or changed
    return changed


def load() -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    if _merge_missing(data, DEFAULTS):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n")

    _config = data
    return _config


def get(path: str, default: Any = None) -> Any:
    value: Any = load()
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def get_bool(path: str, default: bool = False) -> bool:
    value = get(path, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)
