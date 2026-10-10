"""运行器行为验收：使用正式执行链，机制结果与真实模型分列。"""

import asyncio
import hashlib
import os
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from novacode.evaluation.artifacts import extract_patch, scope_status
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.contracts import EvaluationRun, EvaluationTask, fingerprint
from novacode.evaluation.ledger import Ledger, read_records, sha256
from novacode.evaluation.runner import finalize, grade_result, run_task, stop_and_extract
from novacode.evaluation.worker import execute, frozen_engine
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.llm.anthropic_provider import _usage_from_anthropic
from novacode.permission import Decision, Mode

ROOT = Path(__file__).resolve().parents[2]
LIMITS = BudgetLimits(
    seconds=10,
    tokens=100_000,
    provider_calls=8,
    tool_calls=10,
    single_request_tokens=30_000,
    cleanup_seconds=2,
)


def payload():
    return {
        "run_id": "mechanism",
        "campaign_id": "mechanism",
        "repeat_id": 1,
        "config_id": "eager-schema",
        "requests": ["读取 value.txt，并完成修改。"],
        "allowed_tools": ["read_file", "write_file", "edit_file", "bash", "glob", "grep"],
        "permissions": {"allow": ["Write(value.txt)"], "deny": []},
        "limits": asdict(LIMITS),
        "total_limits": asdict(LIMITS),
        "provider": {
            "name": "scripted",
            "model": "mechanism",
            "protocol": "anthropic",
            "api_key": "secret-mechanism",
            "max_retries": 0,
            "timeout": 2,
            "max_output_tokens": 500,
        },
        "usage_rule": {
            "rule_id": "fixture",
            "protocol": "anthropic",
            "cache_mode": "subset",
            "evidence_sha256": sha256({"fixture": 1}),
        },
    }


class Scripted:
    name = "scripted"
    model = "mechanism"

    def __init__(self, call=None, *, slow=False, fail_close=False):
        self.calls = 0
        self.closes = 0
        self.call = call or ToolCall(
            "write", "write_file", '{"path":"value.txt","content":"fixed"}'
        )
        self.slow = slow
        self.fail_close = fail_close
        self.seen = []

    def request_metadata(self, req: Request):
        return {"sdk_max_retries": 0, "timeout_seconds": 2, "max_output_tokens": 500}

    async def stream(self, req: Request):
        self.calls += 1
        self.seen.append(req)
        if self.slow:
            await asyncio.sleep(20)
        usage = _usage_from_anthropic(SimpleNamespace(input_tokens=100, output_tokens=20))
        if self.calls == 1:
            yield StreamEvent(tool_calls=[self.call], usage=usage, done=True)
        else:
            yield StreamEvent(text="finished", usage=usage, done=True)

    async def close(self):
        self.closes += 1
        if self.fail_close:
            raise RuntimeError("close failure")


@pytest.mark.parametrize("repetition", [1, 2])
async def test_fresh_session_state_and_formal_tool_chain(tmp_path, monkeypatch, repetition):
    isolated = tmp_path / "user"
    isolated.mkdir()
    monkeypatch.setattr(Path, "home", lambda: isolated)
    sessions = []
    for index in range(repetition):
        root = tmp_path / f"workspace-{index}"
        output = tmp_path / f"out-{index}"
        root.mkdir()
        output.mkdir()
        (root / "value.txt").write_text("original")
        provider = Scripted()
        result = await execute(payload(), root, output, provider=provider)
        assert result["termination"] == "completed" and result["cleanup"] == "passed"
        assert result["completed_requests"] == len(result["agent_run_ids"]) == 1
        assert (root / "value.txt").read_text() == "fixed"
        assert provider.closes == 1
        assert len(provider.seen[0].messages) == 1
        assert {t.name for t in provider.seen[0].tools} == set(payload()["allowed_tools"]) | {
            "discover_tools"
        }
        records, _ = read_records(output / "ledger.jsonl")
        state = next(r["data"] for r in records if r["kind"] == "initial_state")
        sessions.append(state["session_id"])
        assert state["memory"] == "empty" and state["auxiliary_agents"] is False
        assert "secret-mechanism" not in (output / "ledger.jsonl").read_text()
        assert result["metrics"]["tool_succeeded"] == 1
    assert len(set(sessions)) == repetition


async def test_ask_is_denied_without_bypass_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-user")
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    (root / "value.txt").write_text("original")
    data = payload()
    data["permissions"] = {"allow": [], "deny": []}
    result = await execute(data, root, out, provider=Scripted())
    assert result["termination"] == "completed"
    assert (root / "value.txt").read_text() == "original"
    assert result["metrics"]["tool_denied"] == 1
    records, _ = read_records(out / "ledger.jsonl")
    assert any(r["kind"] == "approval_denied" for r in records)


def test_permissions_keep_plan_blacklist_and_outside_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    engine = frozen_engine(tmp_path, {"allow": ["Bash", "Write"], "deny": []})
    assert (
        engine.check(Mode.DEFAULT, ToolCall("x", "bash", '{"command":"rm -rf /"}'), False)[0]
        == Decision.DENY
    )
    assert (
        engine.check(Mode.PLAN, ToolCall("x", "write_file", '{"path":"a"}'), False)[0]
        == Decision.DENY
    )
    assert (
        engine.check(Mode.DEFAULT, ToolCall("x", "read_file", '{"path":"/outside"}'), True)[0]
        == Decision.DENY
    )


@pytest.mark.parametrize("kind", ["timeout", "unknown", "tool-budget", "close"])
async def test_stops_and_closes_once(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    data = payload()
    provider = Scripted(slow=kind == "timeout", fail_close=kind == "close")
    if kind == "timeout":
        data["limits"]["seconds"] = 0.02
    if kind == "unknown":
        data["usage_rule"] = None
    if kind == "tool-budget":
        data["limits"]["tool_calls"] = 1
        provider.call = ToolCall("x", "not_allowed", "{}")

        async def always_call(req):
            provider.calls += 1
            yield StreamEvent(
                tool_calls=[provider.call],
                usage=_usage_from_anthropic(SimpleNamespace(input_tokens=10, output_tokens=1)),
                done=True,
            )

        provider.stream = always_call
    result = await execute(data, root, out, provider=provider)
    assert provider.closes == 1
    if kind == "close":
        assert result["cleanup"] == "failed"
    else:
        assert result["termination"] == ("timeout" if kind == "timeout" else "budget-exhausted")
    if kind == "unknown":
        assert provider.calls == 1 and result["metrics"]["unknown_usage_requests"] == 1


def make_repo(root):
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode().strip()

    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "test")
    (root / "value.txt").write_text("original")
    git("add", "value.txt")
    git("commit", "-qm", "base")
    return git, git("rev-parse", "HEAD")


def test_patch_captures_commits_new_ignored_and_worktree_without_runtime(tmp_path):
    root = tmp_path / "repo"
    git, base = make_repo(root)
    (root / "value.txt").write_text("committed")
    git("add", "value.txt")
    git("commit", "-qm", "agent commit")
    (root / "value.txt").write_text("final")
    (root / ".gitignore").write_text("new.py\n")
    (root / "new.py").write_text("created")
    (root / ".novacode").mkdir()
    (root / ".novacode" / "state").write_text("runtime")
    patch, names = extract_patch(root, base)
    assert set(names) == {"value.txt", "new.py", ".gitignore"}
    assert "+final" in patch and "+created" in patch and "runtime" not in patch
    assert git("diff", "--cached") == ""


def task_and_run():
    task = EvaluationTask.from_json(
        (ROOT / "evaluation/examples/contracts/single.json").read_text()
    )
    run = EvaluationRun(
        run_id="test",
        campaign_id="test",
        task_id=task.task_id,
        task_sha256=fingerprint(task),
        initial_sha256=task.initial_state.sha256,
        config_id="eager-schema",
        repeat_id=1,
        state="GRADING",
        agent_run_ids=("agent",),
        completed_requests=1,
        termination="completed",
        scope="passed",
        cleanup="passed",
    )
    return task, run


@pytest.mark.parametrize("failure", ["wrong", "timeout", "scope", "cleanup", "missing"])
def test_termination_acceptance_and_strict_are_independent(failure):
    task, run = task_and_run()
    report = {"conditions": dict.fromkeys(task.acceptance_conditions, True)}
    if failure == "wrong":
        report["conditions"][task.acceptance_conditions[0]] = False
    if failure == "missing":
        with pytest.raises(ValueError, match="缺失"):
            grade_result(run, task, {"conditions": {}})
        return
    if failure in ("timeout", "scope", "cleanup"):
        run = replace(
            run,
            **{
                ("termination" if failure == "timeout" else failure): (
                    "timeout" if failure == "timeout" else "failed"
                )
            },
        )
    result = grade_result(run, task, report)
    assert not result.strict_success
    assert result.acceptance == ("failed" if failure == "wrong" else "passed")


def test_live_resolved_is_preserved_after_timeout():
    task, run = task_and_run()
    task = replace(
        task,
        source="live",
        category="issue-fix",
        source_revision="a" * 40,
        problem_keys=(f"issue:{task.repository}#1",),
    )
    result = grade_result(
        replace(run, termination="timeout"),
        task,
        {"conditions": dict.fromkeys(task.acceptance_conditions, True), "live_resolved": True},
    )
    assert result.live_resolved is True and not result.strict_success


@pytest.mark.skipif(os.name != "posix", reason="主集的跨进程预算使用 Linux flock")
def test_campaign_reservations_do_not_refund_or_allow_changed_limits(tmp_path):
    total = replace(LIMITS, tokens=LIMITS.tokens * 2, provider_calls=16, tool_calls=20, seconds=20)
    reserve_run(tmp_path, "pilot", "one", LIMITS, total)
    reserve_run(tmp_path, "pilot", "two", LIMITS, total)
    with pytest.raises(ValueError, match="不足"):
        reserve_run(tmp_path, "pilot", "three", LIMITS, total)
    with pytest.raises(ValueError, match="不能改变"):
        reserve_run(tmp_path, "pilot", "four", LIMITS, replace(total, tokens=999_999))


async def test_cleanup_failure_blocks_judging(tmp_path):
    task, run = task_and_run()
    ledger = Ledger(tmp_path / "ledger.jsonl", "cleanup")
    result = await finalize(replace(run, state="RUNNING"), task, tmp_path, tmp_path, False, ledger)
    assert result.acceptance == "ungradable" and result.cleanup == "failed"
    assert result.scope == "unknown"
    ledger.close()


async def test_stop_before_extract_and_judge_process_order(tmp_path, monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args[1])
        return b"false\n" if args[1] == "inspect" else b""

    monkeypatch.setattr("novacode.evaluation.runner.command", fake)
    ledger = Ledger(tmp_path / "ledger.jsonl", "order")
    assert await stop_and_extract("owned-container", tmp_path, True, payload(), ledger)
    assert calls == ["stop", "inspect", "cp", "cp", "rm"]
    ledger.close()


async def test_missing_asset_is_preflight_block_not_agent_failure(tmp_path):
    task, _ = task_and_run()
    result = await run_task(
        task,
        public_root=tmp_path,
        acceptance_root=tmp_path / "missing",
        runtime_archive=tmp_path / "missing.tar",
        payload=payload(),
        output=tmp_path / "out",
    )
    assert result.state == "PREFLIGHT" and result.termination is None and result.block_reason
    assert not result.agent_run_ids


def test_scope_checks_nested_paths():
    assert scope_status(["faker/providers/en_US/test.py"], ("faker/**",)) == "passed"
    assert scope_status(["test.py"], ()) == "failed"


@pytest.mark.skipif(os.name != "posix", reason="Linux 主集的 shell 进程清理")
async def test_slow_bash_timeout_kills_owned_process(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    data = payload()
    data["permissions"] = {"allow": ["Bash"], "deny": []}
    data["limits"]["seconds"] = 0.2
    provider = Scripted(
        call=ToolCall("slow", "bash", '{"command":"echo $$ > child.pid; sleep 20"}')
    )
    result = await execute(data, root, out, provider=provider)
    assert result["termination"] == "timeout" and result["cleanup"] == "passed"
    pid = int((root / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_initial_ignored_files_are_excluded_only_if_unchanged(tmp_path):
    root = tmp_path / "repo"
    git, base = make_repo(root)
    (root / "installed.txt").write_text("installed")
    initial = {"installed.txt": hashlib.sha256(b"installed").hexdigest()}
    assert extract_patch(root, base, initial)[1] == []
    (root / "installed.txt").write_text("changed")
    assert extract_patch(root, base, initial)[1] == ["installed.txt"]


async def test_hooks_are_dispatched_by_formal_tool_runner(tmp_path, monkeypatch):
    from novacode.hook import Engine, Event

    events = []
    original = Engine.dispatch

    async def trace(self, event, data):
        events.append(event)
        return await original(self, event, data)

    monkeypatch.setattr(Engine, "dispatch", trace)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    await execute(payload(), root, out, provider=Scripted())
    assert Event.PRE_TOOL_USE in events and Event.POST_TOOL_USE in events


async def test_evaluation_tui_starts_styles_and_preserves_fixed_input(tmp_path, monkeypatch):
    from textual.app import App

    from novacode.tui.app import ChatInput

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    root, out = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    monkeypatch.chdir(root)
    data = payload()
    data["requests"] = ["读取 value.txt，并完成修改。\n"]

    async def headless(self):
        async with self.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert self.agent is not None
            text_input = self.query_one("#chat-input", ChatInput)
            text_input.text = data["requests"][0]
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if len(self.conv.messages()) >= 4:
                    break
            self.exit()

    monkeypatch.setattr(App, "run_async", headless)
    provider = Scripted()
    result = await execute(data, root, out, provider=provider, tui=True)
    assert result["termination"] == "completed" and result["completed_requests"] == 1
    assert provider.seen[0].messages[-1].content == data["requests"][0]
    assert result["metrics"]["tool_succeeded"] == 1 and provider.closes == 1


def test_patch_does_not_execute_agent_git_filters(tmp_path):
    root = tmp_path / "repo"
    git, base = make_repo(root)
    marker = tmp_path / "escaped"
    git("config", "filter.evil.clean", f"touch {marker}")
    (root / ".gitattributes").write_text("*.txt filter=evil\n")
    (root / "value.txt").write_text("updated")
    patch, names = extract_patch(root, base)
    assert not marker.exists() and "+updated" in patch
    assert "value.txt" in names
