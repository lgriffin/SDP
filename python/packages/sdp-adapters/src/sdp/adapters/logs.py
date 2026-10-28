"""Structured JSON logs (NFR-005): one object per line carrying every ``extra`` the kernel attaches,
such as experiment id, task id, duration, resource usage and failure reason."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, TextIO

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                doc[key] = value
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, sort_keys=True)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Handler:
    """Route the ``sdp`` logger tree to one JSON handler; returns the handler so callers can remove it."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("sdp")
    for existing in [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    return handler
