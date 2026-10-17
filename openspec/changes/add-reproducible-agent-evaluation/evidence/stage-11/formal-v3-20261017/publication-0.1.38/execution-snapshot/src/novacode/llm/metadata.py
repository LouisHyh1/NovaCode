"""适配器的白名单元数据；不保存配置凭证和请求正文。"""

from importlib.metadata import version
from typing import Any

from novacode.config import ProviderConfig
from novacode.llm import Request


def client_options(cfg: ProviderConfig) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if cfg.max_retries is not None:
        options["max_retries"] = cfg.max_retries
    if cfg.timeout is not None:
        options["timeout"] = cfg.timeout
    return options


def request_metadata(cfg: ProviderConfig, req: Request) -> dict[str, Any]:
    anthropic = cfg.protocol == "anthropic"
    thinking = (
        anthropic
        and cfg.thinking
        and not any(message.tool_calls or message.tool_results for message in req.messages)
    )
    return {
        "protocol": cfg.protocol,
        "sdk_version": version(cfg.protocol),
        "sdk_max_retries": cfg.max_retries,
        "sdk_retry_rule": "explicit" if cfg.max_retries is not None else "sdk-default",
        "timeout_seconds": cfg.timeout,
        "max_output_tokens": cfg.max_output_tokens or (4096 if anthropic else None),
        "thinking_sent": thinking,
        "thinking_budget_tokens": 2048 if thinking else None,
        "thinking_rule": "enabled-without-tool-history" if anthropic else "unsupported",
    }


def raw_usage(raw: Any, protocol: str) -> dict[str, Any]:
    """只读用量字段；缺失保持 None，不以 UI 的兼容零值代替。"""
    if protocol == "anthropic":
        names = (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "reasoning_tokens",
        )
        return {name: getattr(raw, name, None) for name in names}
    return {
        "prompt_tokens": getattr(raw, "prompt_tokens", None),
        "completion_tokens": getattr(raw, "completion_tokens", None),
        "total_tokens": getattr(raw, "total_tokens", None),
        "cached_tokens": getattr(
            getattr(raw, "prompt_tokens_details", None), "cached_tokens", None
        ),
        "reasoning_tokens": getattr(
            getattr(raw, "completion_tokens_details", None), "reasoning_tokens", None
        ),
    }


def ui_count(value: Any) -> int:
    """仅为兼容现有 UI/锚点保留零值；评测读取 raw 中的未知字段。"""
    return value if type(value) is int and value >= 0 else 0
