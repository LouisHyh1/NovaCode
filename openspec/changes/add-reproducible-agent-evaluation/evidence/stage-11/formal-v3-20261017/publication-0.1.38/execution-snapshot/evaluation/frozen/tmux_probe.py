"""tmux产品链的脚本化Provider机制检查；冻结阶段真实模型调用为零。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from evaluation.examples import tmux_contract_probe as probe  # noqa: E402
from novacode.llm import Request, StreamEvent, ToolCall  # noqa: E402


class FreezeProbe(probe.ScriptedProvider):
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
                tool_calls=[ToolCall("freeze-read", "read_file", '{"path":"freeze-summary.json"}')],
                done=True,
            )
        elif self.requests == 2:
            results = [r for m in req.messages for r in m.tool_results]
            assert len(results) == 1 and not results[0].is_error
            data = results[0].content
            assert '"tasks": 48' in data and '"planned": 432' in data
            assert '"formal_model_calls": 0' in data
            self.record({"event": "tool_result", "call_id": "freeze-read", "content": data})
            reply = "冻结机制核验：48 个合同、432 个 PLANNED；正式模型调用为零。"
            self.record({"event": "reply", "text": reply, "provider_kind": "scripted"})
            yield StreamEvent(text=reply, done=True)
        else:
            raise AssertionError("只允许一个工具请求与一次回复")


if __name__ == "__main__":
    setattr(probe, "ScriptedProvider", FreezeProbe)
    asyncio.run(probe.main())
