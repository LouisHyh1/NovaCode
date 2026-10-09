"""第三阶段 tmux 机制验收：真实 TUI/工具/Session，脚本化 Provider。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import novacode.tui.app as tui
from novacode.agent import Agent
from novacode.config import ProviderConfig
from novacode.evaluation.budget import BudgetLimits, BudgetPolicy
from novacode.evaluation.ledger import Ledger, rebuild, sha256
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.usage import UsageRule
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.llm.anthropic_provider import _usage_from_anthropic
from novacode.permission.engine import new_engine
from novacode.session import SessionService
from novacode.tool import new_default_registry


class ScriptedProvider:
    name = "stage3-scripted"
    model = "mechanism-only-no-model"

    def __init__(self) -> None:
        self.requests = 0
        self.close_count = 0

    def request_metadata(self, req: Request) -> dict[str, object]:
        return {
            "sdk_max_retries": 0,
            "timeout_seconds": 10,
            "max_output_tokens": 500,
            "protocol": "anthropic",
            "sdk_version": "scripted-not-sdk",
            "thinking_sent": False,
        }

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.requests += 1
        usage = _usage_from_anthropic(
            SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_creation_input_tokens=10,
                cache_read_input_tokens=30,
            )
        )
        if self.requests == 1:
            assert req.role == "main" and "读取" in req.messages[-1].content
            yield StreamEvent(
                tool_calls=[
                    ToolCall("stage3-one", "read_file", '{"path":"observation-one.txt"}'),
                    ToolCall("stage3-two", "read_file", '{"path":"observation-two.txt"}'),
                ],
                usage=usage,
                done=True,
            )
        elif self.requests == 2:
            results = [r for m in req.messages for r in m.tool_results]
            assert [r.tool_call_id for r in results] == ["stage3-one", "stage3-two"]
            assert all(not r.is_error for r in results)
            assert "业务内容一" in results[0].content and "业务内容二" in results[1].content
            yield StreamEvent(
                text="观测机制验收通过：已读取两个文件，调用 ID 分别为 stage3-one 和 stage3-two。",
                usage=usage,
                done=True,
            )
        elif self.requests == 3:
            assert req.role == "summary"
            yield StreamEvent(
                text="<summary>已读取 observation-one.txt 和 observation-two.txt，"
                "两项工具均成功，用户请求已经完成。</summary>",
                usage=usage,
                done=True,
            )
        else:
            raise AssertionError("只允许两个主请求和一个手动摘要请求")

    async def close(self) -> None:
        self.close_count += 1
        assert self.close_count == 1


async def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    evidence = Path(sys.argv[2]).resolve(strict=True)
    isolated_user = root / "isolated-user"
    isolated_user.mkdir()
    with (
        patch.object(Path, "home", return_value=isolated_user),
        patch("novacode.skills.loader.USER_SKILLS_DIR", str(isolated_user / ".novacode/skills")),
    ):
        await probe(root, evidence)


async def probe(root: Path, evidence: Path) -> None:
    os.chdir(root)
    ledger = Ledger(evidence / "tmux-ledger.jsonl", "stage3-tmux-mechanism")
    provider = ScriptedProvider()
    limits = BudgetLimits(
        seconds=180,
        tokens=200_000,
        provider_calls=6,
        tool_calls=6,
        single_request_tokens=30_000,
        cleanup_seconds=10,
    )
    budget = BudgetPolicy(total=limits, categories={"mechanism": limits}).for_run(
        "mechanism", "full"
    )
    rule = UsageRule(
        "anthropic-cache-separate-v1",
        "anthropic",
        "separate",
        sha256({"fixture": "scripted-usage-100-20-10-30"}),
    )
    observed = ObservedProvider(provider, ledger, budget, usage_rule=rule)
    tui.new_provider = lambda config: observed.borrow()
    tui.Agent = lambda *args, **kwargs: Agent(*args, **kwargs, tool_observer=observed.tool_observer)
    with ledger.phase("initialization"):
        session = SessionService.create(root)
        engine, error = new_engine(str(root))
        assert error is None
        app = tui.NovaCodeApp(
            [ProviderConfig(provider.name, "anthropic", "unused", provider.model)],
            new_default_registry(),
            engine=engine,
            project_root=root,
            session=session,
        )
        assert not app.skill_loader.names()
    try:
        with ledger.phase("agent"):
            await app.run_async()
    finally:
        with ledger.phase("cleanup"):
            await app._shutdown_resources()
            await observed.close()
    metrics = rebuild(ledger.path)
    assert metrics["provider_calls"] == 3
    assert metrics["measured_tokens"] == 480 and metrics["unknown_usage_requests"] == 0
    assert metrics["tool_calls"] == metrics["tool_succeeded"] == 2
    assert provider.close_count == 1
    ledger.append(
        "verification",
        metrics=metrics,
        provider_close_count=1,
        real_model_calls=0,
        evidence_kind="scripted-mechanism",
    )
    ledger.close()
    (evidence / "tmux-verification.json").write_text(
        json.dumps(
            {
                "metrics": metrics,
                "provider_close_count": 1,
                "real_model_calls": 0,
                "evidence_kind": "scripted-mechanism",
                "workspace": str(root),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
