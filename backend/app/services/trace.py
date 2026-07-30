"""JSON-safe values for streamed and persisted agent traces."""

import dataclasses
import enum
import uuid
from datetime import date, datetime
from typing import Any


def trace_value(value: Any) -> Any:
    """Preserve complete node outputs while making them safe for JSON/JSONB."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return trace_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [trace_value(item) for item in value]
    if isinstance(value, (uuid.UUID, datetime, date, enum.Enum)):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
