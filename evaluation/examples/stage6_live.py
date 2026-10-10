"""第六阶段：有界真实模型自动入口和 tmux 产品入口的发现验收。"""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from stage6_tools import CiStatusTool

from novacode import __version__
from novacode.config import load
from novacode.evaluation.budget import BudgetLimits, BudgetPolicy
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.ledger import Ledger, rebuild
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.runner import command
from novacode.evaluation.usage import UsageRule
from novacode.evaluation.worker import RunTracker, close_resources, execute, frozen_engine
from novacode.hook import Engine
from novacode.llm import new_provider
from novacode.mcp.config import Config, ServerConfig
from novacode.mcp.manager import new_manager
from novacode.permission import Outcome
from novacode.session import SessionService
from novacode.tool import new_default_registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.driver import NoAltScreenDriver

REPO = Path(__file__).resolve().parents[2]
FIRST = (
    "本轮只读。请通过固定 CI 查询工具核对构建 BUILD-731 的状态、具体失败原因及是否允许重试。"
    "不要读取本地业务记录或实现代码。最后用 JSON 给出 build_id、status、reason、retry_allowed。"
)
SECOND = "再次通过 CI 查询工具核对刚才的构建状态，回复相同四个 JSON 字段；继续只读。"
REQUESTS = [FIRST, SECOND]
LIMITS = BudgetLimits(
    seconds=240,
    tokens=150_000,
    provider_calls=12,
    tool_calls=16,
    single_request_tokens=20_000,
    cleanup_seconds=15,
)
TOTAL = replace(LIMITS, seconds=1200, tokens=750_000, provider_calls=60, tool_calls=80)


async def run(root: Path, kind: str) -> None:
    source_files = list((REPO / "src").rglob("*.py")) + [REPO / "pyproject.toml", REPO / "uv.lock"]
    (root / "source-identity.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "files": {
                    str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(source_files)
                },
            },
            indent=2,
        )
        + "\n"
    )
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
    registry = new_default_registry()
    registry.register(CiStatusTool())
    # 两入口都在首个模型请求之前建立 MCP 连接，而不是发现后才启动。
    preparation_start = time.monotonic()
    manager = await new_manager(
        Config(
            servers={
                "fixed_ci": ServerConfig(
                    type="stdio",
                    command=sys.executable,
                    args=[str(REPO / "evaluation/examples/stage6_tools.py")],
                )
            }
        ),
        __version__,
    )
    preparation_seconds = time.monotonic() - preparation_start
    try:
        assert len(manager.tools()) == 1 and manager.tools()[0].read_only
        registry.register(manager.tools()[0])
        allowed = [t.name for t in registry.definitions()]
        rule = UsageRule(
            "deepseek-anthropic-cache-separate-stage4-v1",
            "anthropic",
            "separate",
            hashlib.sha256(
                (
                    REPO / "openspec/changes/add-reproducible-agent-evaluation/evidence"
                    "/stage-4/usage-probe/usage.json"
                ).read_bytes()
            ).hexdigest(),
        )
        if kind == "auto":
            payload = {
                "run_id": root.name,
                "config_id": "full",
                "requests": REQUESTS,
                "allowed_tools": allowed,
                "permissions": {"allow": [], "deny": []},
                "limits": asdict(LIMITS),
                "total_limits": asdict(TOTAL),
                "provider": asdict(cfg),
                "usage_rule": asdict(rule),
            }
            result = await execute(payload, workspace, root, registry=registry)
        else:
            result = await product(root, cfg, registry, allowed, rule)
    finally:
        cleanup_start = time.monotonic()
        await manager.close()
    result["mcp_cleanup"] = "passed" if all(t.done() for t in manager._tasks) else "failed"
    result["mcp_preparation_seconds"] = preparation_seconds
    result["mcp_cleanup_seconds"] = time.monotonic() - cleanup_start
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


async def product(root, cfg, registry, allowed, rule):
    ledger = Ledger(root / "ledger.jsonl", root.name, secrets=(cfg.api_key,))
    budget = BudgetPolicy(total=TOTAL, categories={"stage6": LIMITS}, temporary=LIMITS).for_run(
        "stage6", "full"
    )
    observed = ObservedProvider(new_provider(cfg), ledger, budget, usage_rule=rule)
    tracker = RunTracker(ledger)
    hooks = Engine([], ["frozen-empty"])
    session = SessionService.create(root / "workspace")
    engine = frozen_engine(root / "workspace", {"allow": [], "deny": []})
    policy_path = root / "policy.yaml"
    policy_path.write_text(
        "providers:\n- name: local\n  protocol: anthropic\n  model: local\n  api_key: unused\n"
        "features:\n  progressive_tool_schema: true\n"
    )
    features = load(str(policy_path)).features

    class ProbeApp(NovaCodeApp):
        CSS_PATH = str(REPO / "src/novacode/tui/styles.tcss")

        def _assemble_provider_agent(self, config, provider):
            agent = super()._assemble_provider_agent(config, provider)
            agent.set_allowed_tools(allowed)
            agent._tool_runner._observer = observed.tool_observer
            agent._context_manager._observer = lambda trigger, out: ledger.append(
                "context_preparation", trigger=trigger.value, **asdict(out)
            )

            async def deny(request):
                ledger.append("approval_denied", tool=request.name)
                return Outcome.DENY_ONCE, True

            agent._tool_runner._approval_upgrader = deny
            return agent

        async def on_mount(self):
            await super().on_mount()
            ledger.append(
                "ready",
                version=self.agent.version,
                allowed_tools=allowed,
                visible_tools=[t.name for t in self._current_tool_defs()],
                compression=self.agent.context_compression,
                progressive=self.agent.progressive_tool_schema,
                limits=asdict(LIMITS),
                mcp_connected=True,
            )

        async def _consume_events(self, stream):
            await super()._consume_events(tracker.observe(stream))
            await self.session.sync()
            ledger.append("turn_finished", final=ledger.artifact(self.conv.messages()[-1].content))

    with ledger.phase("initialization"):
        app = ProbeApp(
            [cfg],
            registry,
            project_root=root / "workspace",
            session=session,
            engine=engine,
            hook_engine=hooks,
            context_compression=features.context_compression,
            progressive_tool_schema=features.progressive_tool_schema,
            provider_factory=lambda _: observed.borrow(),
            driver_class=NoAltScreenDriver,
        )
    try:
        with ledger.phase("agent"):
            async with asyncio.timeout(budget.remaining_seconds):
                await app.run_async()
    finally:
        await app._shutdown_resources()
        cleanup = await close_resources(session, hooks, observed, ledger, LIMITS.cleanup_seconds)
        ledger.append("session", content=ledger.artifact(session.path.read_text()))
        result = {
            "termination": tracker.termination,
            "cleanup": cleanup,
            "completed_requests": tracker.completed,
            "metrics": rebuild(ledger.path),
        }
        ledger.append("result", **result)
        ledger.close()
    return result


async def drive(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    launcher = root / "launch.py"
    launcher.write_text(
        "import subprocess,sys\nfrom pathlib import Path\n"
        "r=subprocess.run(sys.argv[1:])\n"
        f"Path({str(root / 'exited')!r}).write_text(str(r.returncode))\n"
    )
    tmux = ["tmux", "-L", "novacode-stage6"]
    name = root.parent.parent.name + "-" + root.name
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
                "--kind",
                "tmux",
                "--app",
            ]
        ),
    )

    async def wait_for(kind, count=1):
        for _ in range(480):
            path = root / "ledger.jsonl"
            if path.exists() and path.read_text().count(f'"kind": "{kind}"') >= count:
                return
            if (root / "exited").exists():
                raise RuntimeError("产品入口提前退出")
            await asyncio.sleep(0.5)
        raise TimeoutError(kind)

    try:
        await wait_for("ready")
        for index, request in enumerate(REQUESTS, 1):
            buffer = root / "input.txt"
            buffer.write_text(request)
            await command(*tmux, "load-buffer", str(buffer))
            await command(*tmux, "paste-buffer", "-r", "-p", "-t", name)
            await command(*tmux, "send-keys", "-t", name, "Enter")
            await wait_for("turn_finished", index)
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
    parser.add_argument("--kind", choices=("auto", "tmux"), required=True)
    parser.add_argument("--app", action="store_true")
    args = parser.parse_args()
    if args.kind == "auto":
        args.root.mkdir(parents=True, exist_ok=False)
    asyncio.run(run(args.root, args.kind) if args.kind == "auto" or args.app else drive(args.root))
