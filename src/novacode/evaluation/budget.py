"""同题共享上限、临时上限和 Campaign 总上限的请求准入检查。"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

from novacode.evaluation.contracts import CONFIGS, require, text


class BudgetExceededError(RuntimeError):
    """预算或未知用量阻止追加工作。"""


@dataclass(frozen=True, kw_only=True)
class BudgetLimits:
    seconds: float
    tokens: int
    provider_calls: int
    tool_calls: int
    single_request_tokens: int
    cleanup_seconds: float

    def __post_init__(self) -> None:
        for name in ("tokens", "provider_calls", "tool_calls", "single_request_tokens"):
            value = getattr(self, name)
            require(type(value) is int and value > 0, f"缺少有限正整数预算: {name}")
        for name in ("seconds", "cleanup_seconds"):
            value = getattr(self, name)
            require(
                type(value) in (int, float) and math.isfinite(value) and value > 0,
                f"缺少有限时间预算: {name}",
            )
        require(self.single_request_tokens <= self.tokens, "单请求余量超过总 Token 上限")


class BudgetMeter:
    def __init__(self, limits: BudgetLimits) -> None:
        self.limits = limits
        self.started = time.monotonic()
        self.tokens = 0
        self.reserved = 0
        self.provider_calls = 0
        self.tool_calls = 0
        self.stop_reason = ""

    def check(self, *, reserve: int = 0, provider: int = 0, tool: int = 0) -> None:
        if self.stop_reason:
            raise BudgetExceededError(self.stop_reason)
        if time.monotonic() - self.started >= self.limits.seconds:
            raise BudgetExceededError("time-budget")
        if self.tokens + self.reserved + reserve > self.limits.tokens:
            raise BudgetExceededError("token-budget")
        if self.provider_calls + provider > self.limits.provider_calls:
            raise BudgetExceededError("provider-call-budget")
        if self.tool_calls + tool > self.limits.tool_calls:
            raise BudgetExceededError("tool-call-budget")

    def snapshot(self) -> dict[str, Any]:
        return {
            "limits": asdict(self.limits),
            "known_tokens": self.tokens,
            "reserved_tokens": self.reserved,
            "provider_calls": self.provider_calls,
            "tool_calls": self.tool_calls,
            "stop_reason": self.stop_reason,
        }


class BudgetPolicy:
    """配置 ID 不能提供覆盖值；三配置按任务类别取得同一个合同。"""

    def __init__(
        self,
        *,
        total: BudgetLimits,
        categories: dict[str, BudgetLimits],
        temporary: BudgetLimits | None = None,
    ) -> None:
        require(bool(categories), "缺少类别预算")
        for name in categories:
            text(name, "budget_category")
        self.total = BudgetMeter(total)
        self.categories = dict(categories)
        self.temporary = temporary

    def for_run(self, category: str, config_id: str) -> RunBudget:
        require(config_id in CONFIGS and category in self.categories, "未知配置或预算类别")
        meters = [BudgetMeter(self.categories[category]), self.total]
        if self.temporary is not None:
            meters.insert(0, BudgetMeter(self.temporary))
        return RunBudget(tuple(meters))


class RunBudget:
    def __init__(self, meters: tuple[BudgetMeter, ...]) -> None:
        require(bool(meters), "缺少预算合同")
        self.meters = meters

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, min(m.limits.seconds - (time.monotonic() - m.started) for m in self.meters))

    @property
    def cleanup_seconds(self) -> float:
        return min(m.limits.cleanup_seconds for m in self.meters)

    def reserve_request(self, input_estimate: int, output_limit: int) -> int:
        require(type(input_estimate) is int and input_estimate >= 0, "缺少输入估计")
        require(type(output_limit) is int and output_limit > 0, "缺少请求输出上限")
        reserve = input_estimate + output_limit
        for meter in self.meters:
            if reserve > meter.limits.single_request_tokens:
                raise BudgetExceededError("single-request-reserve")
            meter.check(reserve=reserve, provider=1)
        for meter in self.meters:
            meter.reserved += reserve
            meter.provider_calls += 1
        return reserve

    def settle_request(self, reserve: int, usage: dict[str, Any]) -> None:
        total = usage["measured_total"]
        for meter in self.meters:
            meter.reserved -= reserve
            meter.tokens += usage["known_lower_bound"] if total is None else total
            if total is None:
                meter.stop_reason = "unknown-usage"
            elif total > reserve or meter.tokens + meter.reserved > meter.limits.tokens:
                meter.stop_reason = "actual-token-overrun"

    def start_tool(self) -> None:
        for meter in self.meters:
            meter.check(tool=1)
        for meter in self.meters:
            meter.tool_calls += 1

    def snapshot(self) -> list[dict[str, Any]]:
        return [meter.snapshot() for meter in self.meters]
