"""第十二阶段：独立有界的真实模型 tmux 产品专项，不进入正式主集。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from novacode import __version__
from novacode.agent import Event
from novacode.config import load
from novacode.evaluation.budget import BudgetLimits, BudgetPolicy
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.ledger import Ledger, read_records, rebuild
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.runner import command
from novacode.evaluation.usage import UsageRule
from novacode.evaluation.worker import frozen_engine
from novacode.hook import Engine
from novacode.llm import new_provider
from novacode.permission import Outcome
from novacode.session import SessionInfo, SessionService, load_session
from novacode.tool import new_default_registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.driver import NoAltScreenDriver

REPO = Path(__file__).resolve().parents[2]
LIMITS = BudgetLimits(
    seconds=240,
    tokens=200_000,
    provider_calls=12,
    tool_calls=12,
    single_request_tokens=60_000,
    cleanup_seconds=15,
)
TOTAL = replace(LIMITS, seconds=480, tokens=400_000, provider_calls=24, tool_calls=24)
FIRST = "请调用 read_file 读取 facts.txt，回复其中的验收标识和值；只读。"
DENIED = (
    "请调用 write_file 创建 blocked.txt，内容为 DENIAL-PROBE。"
    "如果审批被拒绝，停止写入，不使用替代工具，只说明结果。"
)
RECALL = "不要读文件或调用工具，依据恢复的对话，回复之前 facts.txt 中的验收标识和值。"
STREAM = "请直接输出从 1 到 2000 的编号，每行附上十个汉字，不调用工具，不省略中间项。"


def records(root: Path, kind: str) -> list[dict[str, Any]]:
    path = root / "ledger.jsonl"
    if not path.exists():
        return []
    return [r["data"] for r in read_records(path)[0] if r["kind"] == kind]


async def trace_stream(
    ledger: Ledger, turn: int, stream: AsyncIterator[Event]
) -> AsyncIterator[Event]:
    seen = False
    async for event in stream:
        if event.text and not seen:
            seen = True
            ledger.append("text_received", turn=turn, sample=event.text)
        if event.err:
            ledger.append("turn_error", error_type=type(event.err).__name__)
        yield event


async def app_run(root: Path) -> None:
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == "deepseek-v4-flash" and p.protocol == "anthropic"
    )
    cfg = replace(cfg, max_retries=0, timeout=90, max_output_tokens=4096, thinking=True)
    workspace = root.parent / "workspace"
    os.environ["HOME"] = str(root.parent / "isolated-user")
    os.chdir(workspace)
    reserve_run(root.parent, root.parent.name, root.name, LIMITS, TOTAL)
    ledger = Ledger(root / "ledger.jsonl", root.name, secrets=(cfg.api_key,))
    budget = BudgetPolicy(total=TOTAL, categories={"product": LIMITS}).for_run(
        "product", "eager-schema"
    )
    usage_path = REPO / (
        "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-4/usage-probe/usage.json"
    )
    observed = ObservedProvider(
        new_provider(cfg),
        ledger,
        budget,
        usage_rule=UsageRule(
            "deepseek-anthropic-cache-separate-stage4-v1",
            "anthropic",
            "separate",
            hashlib.sha256(usage_path.read_bytes()).hexdigest(),
        ),
    )
    session = SessionService.create(workspace)
    hooks = Engine([], ["isolated-empty"])

    class ProbeApp(NovaCodeApp):
        # 仅旁路记录；审批、取消、恢复、退出全部走产品方法和真实按键。
        CSS_PATH = str(REPO / "src/novacode/tui/styles.tcss")

        async def on_mount(self) -> None:
            await super().on_mount()
            assert self.agent is not None
            self.agent._tool_runner._observer = observed.tool_observer
            ledger.append(
                "ready",
                version=__version__,
                session_id=session.session_id,
                limits=asdict(LIMITS),
                total_limits=asdict(TOTAL),
            )

        async def _consume_events(self, stream: AsyncIterator[Event]) -> None:
            turn = len(records(root, "turn_finished")) + 1
            try:
                await super()._consume_events(trace_stream(ledger, turn, stream))
            finally:
                await session.sync()
                ledger.append(
                    "turn_finished",
                    turn=turn,
                    session_id=session.session_id,
                    history=ledger.artifact(session.path.read_text()),
                    state=self.state.value,
                )

        def _mount_approval_block(self) -> None:
            super()._mount_approval_block()
            assert self.pending is not None
            ledger.append("approval_shown", tool=self.pending.name)

        def _commit_approval(self, outcome: Outcome) -> None:
            ledger.append("approval_decision", denied=outcome == Outcome.DENY_ONCE)
            super()._commit_approval(outcome)

        async def resume_session(self, info: SessionInfo) -> bool:
            result = await super().resume_session(info)
            ledger.append("resumed", success=result, session_id=session.session_id)
            return result

        def _finish_cancelled(self) -> None:
            ledger.append("cancelled", pending_text=ledger.artifact("".join(self._text_chunks)))
            super()._finish_cancelled()

    with ledger.phase("initialization"):
        app = ProbeApp(
            [cfg],
            new_default_registry(),
            project_root=workspace,
            session=session,
            engine=frozen_engine(workspace, {"allow": [], "deny": []}),
            hook_engine=hooks,
            provider_factory=lambda _: observed.borrow(),
            driver_class=NoAltScreenDriver,
        )
    try:
        with ledger.phase("agent"):
            async with asyncio.timeout(LIMITS.seconds):
                await app.run_async()
    finally:
        with ledger.phase("cleanup"):
            async with asyncio.timeout(LIMITS.cleanup_seconds):
                await app._shutdown_resources()
                await observed.close()
                ledger.append(
                    "closed",
                    session_closed=session._closed,
                    writer_done=session._worker is None or session._worker.done(),
                    task_consumers=len(app._task_consumers),
                    history=ledger.artifact(session.path.read_text()),
                )
        ledger.close()
        (root / "metrics.json").write_text(json.dumps(rebuild(ledger.path), indent=2) + "\n")


async def drive(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    (root / "workspace").mkdir()
    (root / "isolated-user").mkdir()
    (root / "workspace/facts.txt").write_text("ACCEPTANCE-ID=LYRA-482; VALUE=37\n")
    tmux = ["tmux", "-L", "novacode-stage12"]
    config = root / "tmux.conf"
    config.write_text("set -g remain-on-exit on\n")
    for phase in ("create", "restore"):
        run = root / phase
        run.mkdir()
        name = root.name + "-" + phase
        launcher = run / "launch.py"
        launcher.write_text(
            "import subprocess,sys\nfrom pathlib import Path\n"
            "r=subprocess.run(sys.argv[1:])\n"
            f"Path({str(run / 'exited')!r}).write_text(str(r.returncode))\n"
        )
        await command(
            *tmux,
            "-f",
            str(config),
            "new-session",
            "-d",
            "-s",
            name,
            "-x",
            "160",
            "-y",
            "50",
            shlex.join(
                [
                    sys.executable,
                    str(launcher),
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--root",
                    str(run),
                    "--app",
                ]
            ),
        )

        async def wait_for(kind: str, count: int = 1, turn: int | None = None) -> None:
            async with asyncio.timeout(180):
                while True:
                    found = records(run, kind)
                    if len(found) >= count and (turn is None or found[-1].get("turn") == turn):
                        return
                    if (run / "exited").exists():
                        raise RuntimeError(f"产品提前退出：{phase}/{kind}")
                    await asyncio.sleep(0.1)

        async def send(value: str) -> None:
            buffer = run / "input.txt"
            buffer.write_text(value)
            await command(*tmux, "load-buffer", str(buffer))
            await command(*tmux, "paste-buffer", "-r", "-p", "-t", name)
            await command(*tmux, "send-keys", "-t", name, "Enter")

        async def capture(label: str) -> None:
            (run / f"{label}-pane.txt").write_bytes(
                await command(*tmux, "capture-pane", "-p", "-S", "-", "-t", name)
            )

        try:
            await wait_for("ready")
            if phase == "create":
                await send(FIRST)
                await wait_for("turn_finished")
                await send(DENIED)
                await wait_for("approval_shown")
                await capture("approval")
                await command(*tmux, "send-keys", "-t", name, "n")
                await wait_for("turn_finished", 2)
            else:
                await send("/resume")
                await asyncio.sleep(0.5)
                await capture("resume-menu")
                await command(*tmux, "send-keys", "-t", name, "Enter")
                await wait_for("resumed")
                await send(RECALL)
                await wait_for("turn_finished")
                await send(STREAM)
                await wait_for("text_received", turn=2)
                await command(*tmux, "send-keys", "-t", name, "Escape")
                await wait_for("cancelled")
                await wait_for("turn_finished", 2)
            await capture("final")
        finally:
            await command(*tmux, "send-keys", "-t", name, "C-c")
            async with asyncio.timeout(25):
                while not (run / "exited").exists():
                    await asyncio.sleep(0.1)
        assert (run / "exited").read_text() == "0"


def review(root: Path) -> None:
    """从原账本和 Session 重建专项结论，不重新调用模型。"""
    create, restore = root / "create", root / "restore"
    assert not (root / "workspace/blocked.txt").exists()
    assert (root / "workspace/facts.txt").read_text() == "ACCEPTANCE-ID=LYRA-482; VALUE=37\n"
    assert records(create, "approval_decision") == [{"denied": True}]
    denied = [e for e in records(create, "tool_end") if e["name"] == "write_file"]
    assert len(denied) == 1
    assert denied[0]["authorization_status"] == "denied"
    assert denied[0]["execution"] == "not-executed"
    assert any(e["name"] == "read_file" for e in records(create, "tool_end"))
    assert records(restore, "resumed") == [
        {"success": True, "session_id": records(create, "ready")[0]["session_id"]}
    ]
    assert not records(restore, "tool_start"), "恢复后的回忆不得再次读文件"
    metrics = {}
    for run in (create, restore):
        assert not records(run, "turn_error")
        assert len(records(run, "turn_finished")) == 2
        assert all(e["state"] == "idle" for e in records(run, "turn_finished"))
        first = records(run, "turn_finished")[0]
        history = load_session(run / first["history"]["path"])
        answer = history.messages[-1].content
        assert "LYRA-482" in answer and "37" in answer
        assert records(run, "provider_close") == [{"status": "completed"}]
        closed = records(run, "closed")[0]
        assert closed["session_closed"] and closed["writer_done"]
        assert closed["task_consumers"] == 0
        assert (run / "exited").read_text() == "0"
        # 同时验证每份内容寻址工件和账本，而不只依赖 UI 文本。
        for artifact in (run / "artifacts").glob("*.txt"):
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == artifact.stem
        metrics[run.name] = rebuild(run / "ledger.jsonl")
        assert metrics[run.name]["incomplete_tools"] == 0
        assert metrics[run.name]["incomplete_phases"] == 0
    pending = records(restore, "cancelled")[0]["pending_text"]
    assert (restore / pending["path"]).read_text()
    assert records(restore, "request_end")[-1]["status"] in ("cancelled", "consumer-closed")
    result = {
        "status": "PRODUCT_TMUX_VALID",
        "evidence": "real-model/product-tmux",
        "formal_campaign": False,
        "metrics": metrics,
    }
    (root / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--app", action="store_true")
    parser.add_argument("--review", action="store_true")
    args = parser.parse_args()
    if args.app:
        asyncio.run(app_run(args.root))
    else:
        if not args.review:
            asyncio.run(drive(args.root))
        review(args.root)
