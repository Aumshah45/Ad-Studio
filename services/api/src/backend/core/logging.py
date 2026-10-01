"""structlog JSON logging with secret and PII redaction."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog

from backend.guardrails.pii import redact

_SECRET_MARKERS = ("key", "token", "secret", "password", "authorization")
_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def _scrub(value: Any) -> Any:
    """PII-redact strings, recursing into dicts, lists and tuples (e.g. `flags=[...]`)."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return type(value)(_scrub(v) for v in value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return value


class _CurrentStdout:
    """Writes to whatever `sys.stdout` is now, so cached loggers follow a swapped stream."""

    def write(self, data: str) -> int:
        return sys.stdout.write(data)

    def flush(self) -> None:
        sys.stdout.flush()


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            event_dict[key] = "***"
        else:
            event_dict[key] = _scrub(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS.get(level.upper(), 20)),
        logger_factory=structlog.PrintLoggerFactory(stream or _CurrentStdout()),  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
