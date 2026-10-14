import re
from typing import Any

REDACTED_KEY = re.compile(r"(?i)token|secret|password|credential")


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: "[REDACTED]" if REDACTED_KEY.search(str(key)) else redact(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj
