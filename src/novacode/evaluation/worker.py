"""容器内单主 Agent；凭证仅由 stdin 注入，判题资产不进入此进程。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from novacode import __version__
from novacode.agent import Agent, ApprovalRequest, Event
from novacode.assembly import assemble_agent
from novacode.config import ProviderConfig
from novacode.evaluation.budget import BudgetExceededError, BudgetLimits, BudgetPolicy
from novacode.evaluation.contracts import require
from novacode.evaluation.ledger import Ledger, rebuild
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.usage import UsageRule
from novacode.hook import Engine as HookEngine
from novacode.llm import PromptTooLongError, Provider, new_provider
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine, new_engine
from novacode.permission.settings import PermissionsBlock, Settings, to_rule_set
from novacode.session import SessionService
from novacode.tool import Registry, new_default_registry, with_cwd


def termination(exc: BaseException) -> str:
    if isinstance(exc, BudgetExceededError):
        return "budget-exhausted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, PromptTooLongError):
        return "context-overflow"
    return (
        "provider-error"
        if type(exc).__module__.startswith(("anthropic", "httpx"))
        else "runner-error"
    )


class RunTracker:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.agent_run_ids: list[str] = []
        self.completed = 0
        self.termination = "cancelled"
        self.final = ""

    async def observe(self, stream: AsyncIterator[Event]) -> AsyncIterator[Event]:
        run_id = uuid4().hex
        self.agent_run_ids.append(run_id)
        self.ledger.append("agent_run_start", agent_run_id=run_id)
        failed = False
        try:
            async for event in stream:
                if event.err is not None:
                    failed = True
                    self.termination = termination(event.err)
                    self.ledger.append("agent_error", error_type=type(event.err).__name__)
                if event.notice:
                    self.ledger.append("agent_notice", notice=event.notice)
                    failed = True
                    self.termination = "budget-exhausted"
                if event.compact is not None:
                    self.ledger.append(
                        "compaction",
                        phase=event.compact.phase.value,
                        before=event.compact.before,
                        after=event.compact.after,
                        error_type=type(event.compact.err).__name__ if event.compact.err else "",
                    )
                if event.done and not failed:
                    self.completed += 1
                    self.termination = "completed"
                yield event
        except GeneratorExit:
            raise
        except BaseException as exc:
            self.termination = termination(exc)
            raise
        finally:
            await stream.aclose()  # type: ignore[attr-defined]
            self.ledger.append("agent_run_end", agent_run_id=run_id, termination=self.termination)


def frozen_engine(root: Path, permissions: dict[str, Any]) -> Engine:
    engine, error = new_engine(str(root))
    require(error is None, "隔离权限初始化失败")
    engine.user = to_rule_set(Settings())
    engine.local = to_rule_set(Settings())
    engine.project = to_rule_set(Settings(permissions=PermissionsBlock(**permissions)))
    return engine


async def execute(
    payload: dict[str, Any],
    root: Path,
    output: Path,
    *,
    provider: Provider | None = None,
    tui: bool = False,
) -> dict[str, Any]:
    require(not (root / ".novacode").exists(), "评测只能从无内部状态的初始工作区开始")
    cfg = ProviderConfig(**payload["provider"])
    require(cfg.max_retries == 0 and cfg.timeout is not None, "缺少显式零重试及超时")
    require(payload["config_id"] == "eager-schema", "当前阶段仅支持产品兼容策略")
    registry = new_default_registry()
    allowed = payload["allowed_tools"]
    require(bool(allowed) and set(allowed) <= {t.name for t in registry.definitions()}, "未知工具")
    ledger = Ledger(output / "ledger.jsonl", payload["run_id"], secrets=(cfg.api_key,))
    limits = BudgetLimits(**payload["limits"])
    total = BudgetLimits(**payload["total_limits"])
    budget = BudgetPolicy(total=total, categories={"stage4": limits}, temporary=limits).for_run(
        "stage4", payload["config_id"]
    )
    rule = UsageRule(**payload["usage_rule"]) if payload.get("usage_rule") else None
    observed = ObservedProvider(provider or new_provider(cfg), ledger, budget, usage_rule=rule)
    tracker = RunTracker(ledger)
    session: SessionService | None = None
    hooks = HookEngine([], ["frozen-empty"])
    cleanup = "passed"
    try:
        with ledger.phase("initialization"):
            engine = frozen_engine(root, payload["permissions"])
            session = SessionService.create(root)

            async def deny_ask(request: ApprovalRequest) -> tuple[Outcome, bool]:
                ledger.append("approval_denied", tool=request.name, reason=request.reason)
                return Outcome.DENY_ONCE, True

            def build(p: Provider, tools: Registry, s: SessionService) -> Agent:
                return assemble_agent(
                    p,
                    tools,
                    cfg,
                    s,
                    version=__version__,
                    engine=engine,
                    instructions=payload.get("instructions", ""),
                    memory_index=lambda: "",
                    hook_engine=hooks,
                    allowed_tools=allowed,
                    permission_mode=Mode.DEFAULT,
                    approval_upgrader=deny_ask,
                    tool_observer=observed.tool_observer,
                )

            agent = build(observed.borrow(), registry, session)
            ledger.append(
                "initial_state",
                session_id=session.session_id,
                version=__version__,
                allowed_tools=allowed,
                permissions=payload["permissions"],
                mode="default",
                hooks="frozen-empty",
                memory="empty",
                auxiliary_agents=False,
                instructions=payload.get("instructions", ""),
                strategy="compression-on-schema-eager",
            )
        with ledger.phase("agent"), with_cwd(str(root)):
            if tui:
                await run_tui(
                    cfg,
                    registry,
                    session,
                    engine,
                    hooks,
                    observed,
                    build,
                    tracker,
                    payload,
                    budget.remaining_seconds,
                )
            else:
                await session.start()
                async with asyncio.timeout(budget.remaining_seconds):
                    for request in payload["requests"]:
                        session.conversation.add_user(request)
                        async for event in tracker.observe(
                            agent.run(session.conversation, Mode.DEFAULT, asyncio.Event())
                        ):
                            if event.err is not None:
                                raise event.err
                        await session.sync()
                        if tracker.termination != "completed":
                            break
    except BaseException as exc:
        tracker.termination = termination(exc)
        ledger.append(
            "worker_error", error_type=type(exc).__name__, termination=tracker.termination
        )
    finally:
        cleanup = await close_resources(session, hooks, observed, ledger, budget.cleanup_seconds)
        if session is not None:
            tracker.final = next(
                (
                    m.content
                    for m in reversed(session.conversation.messages())
                    if m.role == "assistant"
                ),
                "",
            )
            ledger.append(
                "session",
                session_id=session.session_id,
                content=ledger.artifact(session.path.read_text()),
            )
        result = {
            "termination": tracker.termination,
            "cleanup": cleanup,
            "agent_run_ids": tracker.agent_run_ids,
            "completed_requests": tracker.completed,
            "final": ledger.artifact(tracker.final),
            "metrics": rebuild(ledger.path),
        }
        ledger.append("worker_result", **result)
        ledger.close()
        (output / "worker-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    return result


async def close_resources(
    session: SessionService | None,
    hooks: HookEngine,
    observed: ObservedProvider,
    ledger: Ledger,
    seconds: float,
) -> str:
    status = "passed"
    with ledger.phase("cleanup"):
        for close in (session.close if session else None, hooks.close, observed.close):
            if close is None:
                continue
            try:
                await asyncio.wait_for(close(), timeout=seconds)
            except BaseException as exc:
                status = "failed"
                ledger.append("cleanup_error", error_type=type(exc).__name__)
    return status


async def run_tui(
    cfg: ProviderConfig,
    registry: Registry,
    session: SessionService,
    engine: Engine,
    hooks: HookEngine,
    observed: ObservedProvider,
    build: Any,
    tracker: RunTracker,
    payload: dict[str, Any],
    seconds: float,
) -> None:
    from novacode.tui.app import NovaCodeApp
    from novacode.tui.driver import NoAltScreenDriver

    class EvaluationApp(NovaCodeApp):
        CSS_PATH = str(Path(__file__).resolve().parents[1] / "tui" / "styles.tcss")

        async def on_mount(self) -> None:
            await super().on_mount()
            tracker.ledger.append("tui_initialized", session_id=self.session.session_id)

        async def _dispatch(self, text: str, display_text: str | None = None) -> None:
            index = len(tracker.agent_run_ids)
            if (
                index >= len(payload["requests"])
                or text != payload["requests"][index].replace("\r\n", "\n").strip()
            ):
                self._show_system("评测只接受合同中的下一条固定请求。")
                return
            await super()._dispatch(payload["requests"][index], display_text)

        async def _consume_events(self, agent_gen: AsyncIterator[Event]) -> None:
            await super()._consume_events(tracker.observe(agent_gen))

    app = EvaluationApp(
        [cfg],
        registry,
        engine=engine,
        hook_engine=hooks,
        project_root=Path.cwd(),
        session=session,
        provider_factory=lambda _: observed.borrow(),
        agent_factory=build,
        driver_class=NoAltScreenDriver,
    )
    try:
        async with asyncio.timeout(seconds):
            await app.run_async()
    finally:
        await app._shutdown_resources()


def main() -> None:
    if "--input" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--input") + 1])
        payload = json.loads(path.read_text())
        path.unlink()
    else:
        payload = json.loads(sys.stdin.readline())
    output = Path("/run/novacode-output")
    output.mkdir(exist_ok=False)
    root = Path("/testbed")
    os.chdir(root)
    asyncio.run(execute(payload, root, output, tui="--tui" in sys.argv))


if __name__ == "__main__":
    main()
