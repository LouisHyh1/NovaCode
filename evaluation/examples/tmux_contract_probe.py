"""在 tmux 使用脚本化 Provider 检查正式 TUI/Agent/工具/Session，不调用模型。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import novacode.tui.app as tui
from novacode.config import ProviderConfig
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission.engine import new_engine
from novacode.session import SessionService
from novacode.tool import new_default_registry


class ScriptedProvider:
    name = "stage2-scripted"
    model = "mechanism-only-no-model"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests = 0
        self.closed = False

    def record(self, event: dict[str, object]) -> None:
        with (self.root / "probe-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.requests += 1
        if self.requests == 1:
            self.record(
                {
                    "event": "request",
                    "user": req.messages[-1].content,
                    "provider_kind": "scripted",
                    "real_model_calls": 0,
                }
            )
            yield StreamEvent(
                tool_calls=[
                    ToolCall("stage2-read", "read_file", '{"path":"contract-summary.json"}')
                ],
                done=True,
            )
        elif self.requests == 2:
            results = [result for message in req.messages for result in message.tool_results]
            assert len(results) == 1 and results[0].tool_call_id == "stage2-read"
            assert not results[0].is_error
            for key, count in (
                ("evaluation_tasks", 2),
                ("planned_evaluation_runs", 2),
                ("planned_agent_runs", 4),
                ("admitted", 0),
            ):
                assert f'"{key}": {count}' in results[0].content
            self.record(
                {
                    "event": "tool_result",
                    "call_id": "stage2-read",
                    "is_error": False,
                    "content": results[0].content,
                }
            )
            reply = (
                "合同机制检查通过：2 个任务、2 个计划评测运行、4 个计划 Agent Run、0 个已入库任务。"
            )
            self.record({"event": "reply", "text": reply, "provider_kind": "scripted"})
            yield StreamEvent(text=reply, done=True)
        else:
            raise AssertionError("样例只允许一个工具请求和一个最终回复")

    async def close(self) -> None:
        assert not self.closed, "Provider 只能关闭一次"
        self.closed = True
        self.record({"event": "close", "stream_requests": self.requests, "real_model_calls": 0})


async def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    os.chdir(root)
    provider = ScriptedProvider(root)
    tui.new_provider = lambda config: provider
    engine, error = new_engine(str(root))
    assert error is None
    session = SessionService.create(root)
    app = tui.NovaCodeApp(
        [ProviderConfig("stage2-scripted", "openai", "unused", provider.model)],
        new_default_registry(),
        engine=engine,
        project_root=root,
        session=session,
    )
    await app.run_async()
    await app._shutdown_resources()
    assert provider.closed and provider.requests == 2


if __name__ == "__main__":
    asyncio.run(main())
