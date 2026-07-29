"""ch12 Hook 系统的核心行为测试。"""

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from novacode.agent import Agent, Phase
from novacode.command.builtin_hooks import handle_hooks
from novacode.command.ui import NopUI
from novacode.hook import Engine, Event, is_blocking, load
from novacode.hook.executor import Executor
from novacode.hook.matcher import eval_condition, get_by_path
from novacode.hook.rule import (
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from novacode.llm import ToolCall
from novacode.permission import Mode
from novacode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
)
from novacode.permission.rule import parse_rule
from novacode.permission.settings import PermissionsBlock, Settings, to_rule_set
from novacode.tool import Registry, Result


def _blocking_shell_command(reason: str) -> str:
    if os.name == "nt":
        return f"1>&2 <nul set /p={reason}& exit /b 2"
    return f"echo {reason} >&2; exit 2"


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("=git status", "git status", True),
        ("=git status", "git status -s", False),
        ("~^npm (install|test)$", "npm install", True),
        ("~^npm (install|test)$", "npm run dev", False),
        ("!=foo", "bar", True),
        ("!=foo", "foo", False),
        ("!~^rm", "ls -lh", True),
        ("!~^rm", "rm -rf .", False),
        ("!git *", "npm test", True),
        ("!git *", "git status", False),
    ],
)
def test_permission_matchers(pattern: str, value: str, expected: bool) -> None:
    assert compile_matcher(pattern, is_command=True).match(value) is expected


def test_permission_rule_prefixes_and_error_log(capsys: pytest.CaptureFixture[str]) -> None:
    exact, ok = parse_rule("Bash(=git status)")
    assert ok and exact.matcher is not None
    assert exact.matcher.match("git status")
    assert not exact.matcher.match("git status -s")

    rules = to_rule_set(
        Settings(permissions=PermissionsBlock(allow=["Bash(~[invalid)", "Bash(git *)"]))
    )
    assert len(rules.allow) == 1
    assert "parse failed" in capsys.readouterr().err


def test_condition_nested_path_and_combine_modes() -> None:
    payload = {"tool_input": {"path": "src/main.py"}, "is_error": False}
    condition = Condition(
        CombineMode.ALL_OF,
        [
            AtomCondition("tool_input.path", GlobMatcher("**/*.py")),
            AtomCondition("is_error", ExactMatcher("False")),
            AtomCondition("missing", NotMatcher(ExactMatcher("present"))),
        ],
    )
    assert get_by_path(payload, "tool_input.path") == "src/main.py"
    assert get_by_path(payload, "missing.path") == ""
    assert eval_condition(condition, payload)
    assert not eval_condition(
        Condition(
            CombineMode.ANY_OF,
            [AtomCondition("is_error", ExactMatcher("True"))],
        ),
        payload,
    )


def test_load_merges_files_and_skips_invalid_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    (project / ".novacode").mkdir(parents=True)
    (home / ".novacode").mkdir(parents=True)
    (project / ".novacode" / "hooks.yaml").write_text(
        """hooks:
  - name: project-start
    event: SessionStart
    action: {type: prompt, text: zh-CN}
  - name: bad-async
    event: PreToolUse
    async: true
    action: {type: shell, command: echo x}
  - name: bad-condition
    event: Stop
    if: {all_of: [], any_of: []}
    action: {type: shell, command: echo x}
""",
        encoding="utf-8",
    )
    (home / ".novacode" / "hooks.yaml").write_text(
        """hooks:
  - name: user-stop
    event: Stop
    timeout: 5s
    action: {type: http, url: 'https://example.test/done'}
  - name: project-start
    event: SessionEnd
    action: {type: shell, command: echo duplicate}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    engine = load(project)
    assert [rule.name for rule in engine.rules] == ["project-start", "user-stop"]
    assert len(engine.sources) == 2
    error = capsys.readouterr().err
    assert "async not allowed for blocking events" in error
    assert "exactly one of all_of or any_of" in error
    assert "duplicate name" in error
    asyncio.run(engine.close())


@pytest.mark.asyncio
async def test_executor_shell_prompt_http_and_subagent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    executor = Executor()
    blocked = await executor.run(
        Rule("block", Event.PRE_TOOL_USE, ShellAction(_blocking_shell_command("blocked"))),
        {"z": 1, "a": 2},
        blocking=True,
    )
    assert blocked.blocked and blocked.reason == "blocked"
    failed = await executor.run(Rule("bad", Event.STOP, ShellAction("exit 1")), {}, blocking=False)
    assert failed.err is not None and not failed.blocked
    succeeded = await executor.run(
        Rule("log", Event.STOP, ShellAction("echo visible >&2")), {}, blocking=False
    )
    assert succeeded.err is None
    assert "visible" in capsys.readouterr().err
    prompted = await executor.run(
        Rule("p", Event.SESSION_START, PromptAction("use zh-CN")),
        {},
        blocking=False,
    )
    assert prompted.prompt == "use zh-CN"
    await executor.run(
        Rule("stub", Event.STOP, SubagentAction("foo", "test")),
        {},
        blocking=False,
    )
    assert "skipped: stub" in capsys.readouterr().err
    await executor.close()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.content == b'{"event": "PreToolUse"}'
        return httpx.Response(200, json={"decision": "block", "reason": "policy"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    http_executor = Executor(client)
    outcome = await http_executor.run(
        Rule(
            "network",
            Event.PRE_TOOL_USE,
            HttpAction("https://example.test/check"),
        ),
        {"event": "PreToolUse"},
        blocking=True,
    )
    assert outcome.blocked and outcome.reason == "policy"
    await client.aclose()


@pytest.mark.asyncio
async def test_engine_order_block_prompt_and_only_once() -> None:
    rules = [
        Rule(
            "once",
            Event.SESSION_START,
            PromptAction("first"),
            only_once=True,
        ),
        Rule("block", Event.PRE_TOOL_USE, ShellAction(_blocking_shell_command("no"))),
        Rule("after", Event.PRE_TOOL_USE, PromptAction("must-not-run")),
    ]
    engine = Engine(rules, ["hooks.yaml"])
    first = await engine.dispatch(Event.SESSION_START, {})
    second = await engine.dispatch(Event.SESSION_START, {})
    assert first.injected_prompts == ["first"]
    assert second.injected_prompts == []
    await engine.reset_for_new_session()
    assert (await engine.dispatch(Event.SESSION_START, {})).injected_prompts == ["first"]
    blocked = await engine.dispatch(Event.PRE_TOOL_USE, {})
    assert blocked.blocked and blocked.blocking_hook_name == "block"
    assert blocked.injected_prompts == []
    assert is_blocking(Event.PRE_TOOL_USE)
    assert not is_blocking(Event.STOP)
    await engine.close()


class _WriteTool:
    read_only = False

    def __init__(self) -> None:
        self.executed = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        self.executed = True
        return Result("written")


@pytest.mark.asyncio
async def test_agent_pre_tool_hook_blocks_before_permission_and_execution() -> None:
    tool = _WriteTool()
    registry = Registry()
    registry.register(tool)
    hook_engine = Engine(
        [
            Rule(
                "block-write",
                Event.PRE_TOOL_USE,
                ShellAction(_blocking_shell_command("blocked")),
                Condition(
                    CombineMode.ALL_OF,
                    [AtomCondition("tool_name", ExactMatcher("write_file"))],
                ),
            )
        ],
        [],
    )
    agent = Agent(object(), registry, hook_engine=hook_engine)  # type: ignore[arg-type]
    agent._event_queue = asyncio.Queue()
    results, completed = await agent._execute_batched(
        [ToolCall("t1", "write_file", '{"path":"x.txt"}')],
        asyncio.Event(),
        Mode.DEFAULT,
    )
    events = [agent._event_queue.get_nowait(), agent._event_queue.get_nowait()]
    assert completed and not tool.executed
    assert results[0].is_error
    assert results[0].content == "[hook block-write] blocked"
    assert [event.tool.phase for event in events] == [Phase.START, Phase.END]
    await hook_engine.close()


class _HookUI(NopUI):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.output = ""

    def println(self, message: str) -> None:
        self.output = message

    def hook_rules(self) -> list[Rule]:
        return self.engine.rules

    def hook_sources(self) -> list[str]:
        return self.engine.sources


@pytest.mark.asyncio
async def test_hooks_command_format() -> None:
    engine = Engine(
        [
            Rule(
                "notify",
                Event.STOP,
                PromptAction("done"),
                only_once=True,
            )
        ],
        ["/project/.novacode/hooks.yaml"],
    )
    ui = _HookUI(engine)
    await handle_hooks(ui)
    assert "notify  Stop  prompt [once]" in ui.output
    assert "Loaded from: /project/.novacode/hooks.yaml" in ui.output
    await engine.close()


def test_matcher_classes_are_constructible() -> None:
    assert ExactMatcher("x").match("x")
    assert GlobMatcher("**/*.py").match("src/a.py")
    assert RegexMatcher("^x", __import__("re").compile("^x")).match("xyz")
    assert NotMatcher(ExactMatcher("x")).match("")
