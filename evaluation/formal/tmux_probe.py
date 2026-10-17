"""tmux 中的正式 TUI/Agent/工具/Session 机制验收，使用脚本化 Provider。"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from evaluation.examples import tmux_contract_probe as probe  # noqa: E402
from novacode.llm import Request, StreamEvent, ToolCall  # noqa: E402


class ChainProbe(probe.ScriptedProvider):
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
                    ToolCall("chain-read", "read_file", '{"path":"evidence-summary.json"}')
                ],
                done=True,
            )
        elif self.requests == 2:
            results = [r for message in req.messages for r in message.tool_results]
            assert len(results) == 1 and not results[0].is_error
            for text in (
                '"history_runs": 147',
                '"chain_cases": 10',
                '"selected_tasks": 24',
                '"real_model_calls": 0',
            ):
                assert text in results[0].content
            self.record(
                {"event": "tool_result", "call_id": "chain-read", "content": results[0].content}
            )
            reply = "历史审计 147 次，离线链路 10 例验证通过，选中 24 题；真实模型调用为零。"
            self.record({"event": "reply", "text": reply, "provider_kind": "scripted"})
            yield StreamEvent(text=reply, done=True)
        else:
            raise AssertionError("只允许读取证据和一次机制回复")


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve(strict=True)
    isolated = root / "isolated-user"
    isolated.mkdir()
    os.environ["HOME"] = str(isolated)
    setattr(probe, "ScriptedProvider", ChainProbe)
    asyncio.run(probe.main())
    (root / "exited.ok").write_text("scripted-provider; real_model_calls=0\n")
