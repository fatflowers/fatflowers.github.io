"""Minimal structured logging that never includes credentials or content."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


REDACTED_KEYS = {"authorization", "token", "api_key", "apikey", "secret", "content_text"}


def new_run_id() -> str:
    return str(uuid4())


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in REDACTED_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def emit_event(run_id: str, event: str, **fields: Any) -> None:
    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": fields.pop("level", "info"),
        "run_id": run_id,
        "event": event,
    }
    record.update(_redact(fields))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), file=sys.stderr)
