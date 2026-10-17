"""运行记录的凭证脱敏；已知凭证由装配方显式传入。"""

import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|"
    r"passwd|secret|credential|cookie)",
    re.IGNORECASE,
)
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[^\s\"']+", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)"
    r"\s*[\"']?\s*[=:]\s*[\"']?)[^\s,;\"']+",
    re.IGNORECASE,
)


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if _SECRET_KEY.search(str(key)) and str(key) != "authorization_status"
            else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = _AUTH.sub(r"\1 [REDACTED]", value)
        return _ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return value
