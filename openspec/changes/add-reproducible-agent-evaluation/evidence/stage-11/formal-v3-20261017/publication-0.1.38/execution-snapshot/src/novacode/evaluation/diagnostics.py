"""保留有界异常事实；不保存请求头、完整响应或操作者配置。"""

from __future__ import annotations

from typing import Any

from novacode.privacy import redact


def error_details(error: BaseException, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        data: dict[str, Any] = {
            "type": type(current).__name__,
            "message": str(redact(str(current), secrets))[:2000],
        }
        status = getattr(current, "status_code", None)
        if type(status) is int:
            data["http_status"] = status
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            item = body.get("error", body)
            if isinstance(item, dict):
                data["provider_error"] = {
                    key: str(redact(str(item[key]), secrets))[:500]
                    for key in ("type", "code", "message")
                    if key in item
                }
        chain.append(data)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return {"chain": chain}
