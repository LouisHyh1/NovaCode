"""只有带核验依据的规则才能把原始用量解释为实测总量。"""

from dataclasses import dataclass
from typing import Any

from novacode.evaluation.contracts import digest, require, text
from novacode.llm import Usage


@dataclass(frozen=True)
class UsageRule:
    rule_id: str
    protocol: str
    cache_mode: str
    evidence_sha256: str
    reasoning_mode: str = "subset"

    def __post_init__(self) -> None:
        text(self.rule_id, "usage rule")
        require(self.protocol in ("anthropic", "openai"), "未知用量协议")
        require(self.cache_mode in ("separate", "subset"), "未知缓存语义")
        require(self.reasoning_mode in ("separate", "subset"), "未知推理用量语义")
        digest(self.evidence_sha256)


def count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize(usage: Usage | None, rule: UsageRule | None) -> dict[str, Any]:
    raw = {} if usage is None else usage.raw
    result: dict[str, Any] = {
        "raw": raw,
        "adapter_rule": None if usage is None else usage.normalization,
        "verified_rule": None if rule is None else rule.rule_id,
        "evidence_sha256": None if rule is None else rule.evidence_sha256,
        "measured_total": None,
        "known_lower_bound": 0,
        "completeness": "unknown",
    }
    if usage is None or rule is None or usage.protocol != rule.protocol:
        return result
    names = (
        ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        if rule.protocol == "anthropic"
        else ("prompt_tokens", "completion_tokens", "cached_tokens")
    )
    values = [count(raw.get(name)) for name in names]
    required = values if rule.cache_mode == "separate" else values[:2]
    if rule.reasoning_mode == "separate":
        required.append(count(raw.get("reasoning_tokens")))
    known = sum(value for value in required if value is not None)
    result["known_lower_bound"] = known
    if all(value is not None for value in required):
        result["measured_total"] = known
        result["completeness"] = "complete"
    else:
        result["completeness"] = "partial" if known else "unknown"
    # 未知、负值和协议不一致保持可观察，不能由兼容 UI 零值制造实测。
    return result
