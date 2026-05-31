import builtins
import logging
import os
import sys
import threading
from datetime import datetime
from functools import wraps
from inspect import signature
from time import perf_counter
from typing import Any, Callable


_original_print = builtins.print
_original_excepthook = sys.excepthook
_original_threading_excepthook = getattr(threading, "excepthook", None)

_logger: logging.Logger | None = None
_log_path: str | None = None
_configured = False
_socket_verbose = os.environ.get("LINEAGE_LOG_SOCKET_VERBOSE") == "1"
_MAX_FIELD_REPR = 800


def setup(role: str) -> str:
    global _logger, _log_path, _configured

    if _configured and _log_path:
        return _log_path

    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_path = os.path.join(log_dir, f"{role}_{timestamp}_{os.getpid()}.log")

    logger = logging.getLogger(f"lineage_macro.{role}.{os.getpid()}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.FileHandler(_log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] "
            "[pid=%(process)d tid=%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    _logger = logger

    def print_and_log(*args, **kwargs):
        _original_print(*args, **kwargs)
        file = kwargs.get("file", sys.stdout)
        if file not in (sys.stdout, sys.stderr):
            return

        sep = kwargs.get("sep", " ")
        msg = sep.join(str(arg) for arg in args)
        level = logging.ERROR if file is sys.stderr else logging.INFO
        if msg:
            for line in msg.splitlines():
                logger.log(level, line)
        else:
            logger.log(level, "")

    def excepthook(exc_type, exc_value, exc_traceback):
        logger.exception("uncaught_exception", exc_info=(exc_type, exc_value, exc_traceback))
        _original_excepthook(exc_type, exc_value, exc_traceback)

    def threading_excepthook(args):
        logger.exception(
            f"uncaught_thread_exception thread={getattr(args.thread, 'name', None)}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if _original_threading_excepthook:
            _original_threading_excepthook(args)

    builtins.print = print_and_log
    sys.excepthook = excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = threading_excepthook

    _configured = True
    event("logger_ready", role=role, path=_log_path, socket_verbose=_socket_verbose)
    return _log_path


def setup_process(role: str, **fields: Any) -> str:
    """Set up logging and emit the standard process-start boundary once."""
    log_path = setup(role)
    print(f"[{role}] 로그 파일: {log_path}")
    event(f"{role}_process_start", **fields)
    return log_path


def event(name: str, **fields: Any) -> None:
    if _logger is None:
        return
    if not fields:
        _logger.debug(name)
        return

    parts = [name]
    for key, value in fields.items():
        text = repr(value)
        if len(text) > _MAX_FIELD_REPR:
            text = f"{text[:_MAX_FIELD_REPR]}...<truncated>"
        parts.append(f"{key}={text}")
    _logger.debug(" ".join(parts))


def _peer(conn: Any) -> str:
    try:
        return repr(conn.getpeername())
    except OSError:
        return "<disconnected>"
    except Exception:
        return "<unknown>"


def _socket_is_chatty(message: dict | None) -> bool:
    if not isinstance(message, dict):
        return False
    cmd = message.get("cmd")
    status = message.get("status")
    return cmd == "ping" or status == "pong"


def _is_socket_like(value: Any) -> bool:
    return callable(getattr(value, "getpeername", None)) and callable(getattr(value, "recv", None))


def _summarize(value: Any) -> Any:
    if _is_socket_like(value):
        return _peer(value)
    if isinstance(value, dict):
        keys = ("cmd", "status", "req_id", "idx", "target", "nickname", "reason", "addr", "dx", "dy")
        compact = {key: value[key] for key in keys if key in value}
        if compact and not _socket_verbose:
            return compact
        if "conn" in value and "idx" in value:
            return {"idx": value.get("idx"), "addr": value.get("addr")}
    if isinstance(value, tuple):
        return tuple(_summarize(item) for item in value)
    if isinstance(value, list):
        return [_summarize(item) for item in value[:10]]
    return value


def _is_chatty_trace(fields: dict[str, Any], result: Any) -> bool:
    if _socket_verbose:
        return False
    if result is None or result is False:
        return False
    return any(_socket_is_chatty(value) for value in fields.values()) or _socket_is_chatty(result)


def _trace_fields(sig: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    return {key: _summarize(value) for key, value in bound.arguments.items()}


def trace(func: Callable[..., Any]):
    sig = signature(func)
    event_name = func.__name__

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        fields = _trace_fields(sig, args, kwargs)
        started_at = perf_counter()
        try:
            value = func(*args, **kwargs)
        except Exception as exc:
            elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
            event(f"{event_name}_error", **fields, error=repr(exc), elapsed_ms=elapsed_ms)
            raise

        if not _is_chatty_trace(fields, value):
            elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
            event(f"{event_name}_done", **fields, result=_summarize(value), elapsed_ms=elapsed_ms)
        return value

    return wrapper
