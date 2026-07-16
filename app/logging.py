"""Small JSON log formatter that excludes application secrets by construction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    SAFE_EXTRAS = ("request_id", "run_id", "project_id", "stage", "duration_ms")

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in self.SAFE_EXTRAS:
            value = getattr(record, key, None)
            if value is not None:
                event[key] = value
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False, default=str)
