"""第五阶段真实产品 tmux 验收：固定请求、隔离状态、有界费用。"""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
from dataclasses import asdict, replace
from pathlib import Path

from novacode.config import load
from novacode.evaluation.budget import BudgetLimits, BudgetPolicy
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.ledger import Ledger, rebuild, sha256
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.runner import command
from novacode.evaluation.usage import UsageRule
from novacode.evaluation.worker import RunTracker, close_resources, frozen_engine
from novacode.hook import Engine
from novacode.llm import new_provider
from novacode.session import SessionService
from novacode.tool import new_default_registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.driver import NoAltScreenDriver

REPO = Path(__file__).resolve().parents[2]
FIRST = (
    "记住本轮约束：只读，不修改业务文件。请先调用 read_file 读取 facts.txt，"
    "再用合法工具核对最后一行，回答末行的标识和值。"
)
SECOND = "继续遵守早期约束。回复刚才的末行标识和值，以及本轮是否允许修改业务文件。"
OVERFLOW = "继续处理以下新增资料，读完只回复完成：\n" + " x" * 1_100_000
LIMITS = BudgetLimits(
    seconds=240,
    tokens=1_400_000,
    provider_calls=12,
    tool_calls=12,
    single_request_tokens=900_000,
    cleanup_seconds=15,
)
TOTAL = replace(LIMITS, seconds=900, tokens=4_200_000, provider_calls=36, tool_calls=36)


async def app_run(root: Path, mode: str) -> None:
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == "deepseek-v4-flash" and p.protocol == "anthropic"
    )
    cfg = replace(cfg, max_retries=0, timeout=90, max_output_tokens=4096, thinking=True)
    reserve_run(root.parent, root.parent.parent.name, root.name, LIMITS, TOTAL)
    workspace = root / "workspace"
    workspace.mkdir()
    isolated = root / "isolated-user"
    isolated.mkdir()
    os.environ["HOME"] = str(isolated)
    os.chdir(workspace)
    facts = "".join(
        f"record-{i:04d}: " + "configuration documentation " * 4 + "\n" for i in range(1100)
    )
    facts += "FINAL-ID=ORBIT-731; VALUE=29\n"
    (workspace / "facts.txt").write_text(facts)
    ledger = Ledger(root / "ledger.jsonl", root.name, secrets=(cfg.api_key,))
    budget = BudgetPolicy(total=TOTAL, categories={"stage5": LIMITS}, temporary=LIMITS).for_run(
        "stage5", "eager-schema"
    )
    usage_path = (
        REPO
        / "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-4"
        / "usage-probe/usage.json"
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
    tracker = RunTracker(ledger)
    hooks = Engine([], ["frozen-empty"])
    session = SessionService.create(workspace)
    engine = frozen_engine(workspace, {"allow": ["Bash"], "deny": []})
    config_path = root / "policy.yaml"
    # 通过真实配置解析装配；开启样本故意省略新字段以核验兼容默认。
    config_path.write_text(
        "providers:\n- name: fixture\n  protocol: anthropic\n"
        "  model: fixture\n  api_key: unused\n"
        + ("" if mode == "on" else "features:\n  context_compression: false\n")
    )
    compression = load(str(config_path)).features.context_compression

    class ProbeApp(NovaCodeApp):
        CSS_PATH = str(REPO / "src/novacode/tui/styles.tcss")

        async def on_mount(self):
            await super().on_mount()
            self.agent._context_manager._observer = lambda trigger, result: ledger.append(
                "context_preparation", trigger=trigger.value, **asdict(result)
            )
            self.agent._tool_runner._observer = observed.tool_observer
            ledger.append(
                "ready",
                compression=self.agent.context_compression,
                context_window=self.agent.context_window,
                tools=[asdict(t) for t in self._current_tool_defs()],
                facts_sha256=sha256(facts),
                limits=asdict(LIMITS),
                output_limit=cfg.max_output_tokens,
                auxiliary_agents=False,
            )

        async def _consume_events(self, stream):
            await super()._consume_events(tracker.observe(stream))
            await self.session.sync()
            ledger.append(
                "turn_finished",
                final=ledger.artifact(self.conv.messages()[-1].content),
                message_count=self.conv.length(),
            )

        async def force_compact(self):
            await super().force_compact()
            await self.session.sync()
            ledger.append("manual_finished", history=ledger.artifact(session.path.read_text()))

    app = ProbeApp(
        [cfg],
        new_default_registry(),
        project_root=workspace,
        session=session,
        engine=engine,
        hook_engine=hooks,
        context_compression=compression,
        provider_factory=lambda _: observed.borrow(),
        driver_class=NoAltScreenDriver,
    )
    try:
        async with asyncio.timeout(budget.remaining_seconds):
            await app.run_async()
    finally:
        await app._shutdown_resources()
        cleanup = await close_resources(session, hooks, observed, ledger, LIMITS.cleanup_seconds)
        ledger.append("session", content=ledger.artifact(session.path.read_text()))
        result = {
            "mode": mode,
            "termination": tracker.termination,
            "cleanup": cleanup,
            "completed_requests": tracker.completed,
            "metrics": rebuild(ledger.path),
        }
        ledger.append("result", **result)
        ledger.close()
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


async def drive(root: Path, mode: str) -> None:
    root.mkdir(parents=True, exist_ok=False)
    launcher = root / "launch.py"
    launcher.write_text(
        "import subprocess,sys\nfrom pathlib import Path\n"
        "r=subprocess.run(sys.argv[1:])\n"
        f"Path({str(root / 'exited')!r}).write_text(str(r.returncode))\n"
    )
    tmux = ["tmux", "-L", "novacode-stage5"]
    name = root.parent.parent.name + "-" + mode
    config = root / "tmux.conf"
    config.write_text("set -g remain-on-exit on\n")
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
                str(root),
                "--mode",
                mode,
                "--app",
            ]
        ),
    )

    async def wait_for(kind: str, count: int = 1):
        for _ in range(240):
            path = root / "ledger.jsonl"
            if path.exists() and path.read_text().count(f'"kind": "{kind}"') >= count:
                return
            if (root / "exited").exists():
                raise RuntimeError("产品入口提前退出")
            await asyncio.sleep(0.5)
        raise TimeoutError(kind)

    async def send(text: str):
        buffer = root / "input.txt"
        buffer.write_text(text)
        await command(*tmux, "load-buffer", str(buffer))
        await command(*tmux, "paste-buffer", "-r", "-p", "-t", name)
        await command(*tmux, "send-keys", "-t", name, "Enter")

    try:
        await wait_for("ready")
        await send(FIRST)
        await wait_for("turn_finished")
        await send("/compact")
        await wait_for("manual_finished")
        (root / "manual-pane.txt").write_bytes(
            await command(*tmux, "capture-pane", "-p", "-S", "-", "-t", name)
        )
        if mode == "overflow":
            # 固定脚本新增资料，经产品请求入口累积，不改模型窗口或工具返回限制。
            await send(OVERFLOW)
        else:
            await send(SECOND)
        await wait_for("turn_finished", 2)
        (root / "tmux-pane.txt").write_bytes(
            await command(*tmux, "capture-pane", "-p", "-S", "-", "-t", name)
        )
    finally:
        await command(*tmux, "send-keys", "-t", name, "C-c")
        for _ in range(30):
            if (root / "exited").exists():
                break
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("on", "off", "overflow"), required=True)
    parser.add_argument("--app", action="store_true")
    args = parser.parse_args()
    asyncio.run(app_run(args.root, args.mode) if args.app else drive(args.root, args.mode))
