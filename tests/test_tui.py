"""Tests for TUI approval interaction — Ch06 permission UI."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from novacode.agent import Agent, ApprovalRequest, CompactPhase, Event, SessionRuntime
from novacode.command.builtin_prompt import REVIEW_DIRECTIVE
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from novacode.config import ProviderConfig
from novacode.hook import Engine as HookEngine
from novacode.hook import Event as HookEvent
from novacode.hook.rule import Rule as HookRule
from novacode.hook.rule import SubagentAction
from novacode.llm import Message, StreamEvent
from novacode.memory import MemoryExtractor, MemoryGovernor, MemoryKind, MemoryStore, MemoryTurn
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.rule import RuleSet
from novacode.session import (
    SessionService,
    SessionWriteError,
    SessionWriter,
    list_sessions,
    load_session,
)
from novacode.subagent import load_catalog
from novacode.tool import Registry
from novacode.tui.app import (
    ChatInput,
    NovaCodeApp,
    SessionState,
    _outcome_for_index,
)
from novacode.tui.commands import format_compact_notice
from novacode.tui.resume import format_session_option

# ── helpers ──────────────────────────────────────────────────


def _make_engine() -> Engine:
    return Engine(
        root=".",
        blacklist=[],
        user=RuleSet(),
        project=RuleSet(),
        local=RuleSet(),
        local_path="",
        _start_mode=Mode.BYPASS,
    )


def _make_app(
    session: SessionService | None = None,
    project_root: Path | None = None,
) -> NovaCodeApp:
    """Create a minimal NovaCodeApp suitable for testing."""
    cfg = ProviderConfig(
        name="test",
        protocol="openai",
        api_key="sk-test",
        base_url="http://localhost:8000",
        model="gpt-4",
    )
    return NovaCodeApp(
        providers=[cfg],
        registry=Registry(),
        version="test",
        engine=_make_engine(),
        session=session,
        project_root=project_root,
    )


@pytest.mark.asyncio
async def test_coordinator_mode_status_and_tool_lock() -> None:
    app = _make_app()
    app.coordinator_mode = True
    async with app.run_test(size=(100, 30)):
        assert "[COORDINATOR]" in str(app.query_one("#mode-label").render())
        assert app.agent is not None
        assert "bash" in app.agent.allowed_tools
        assert "write_file" not in app.agent.allowed_tools
        assert "edit_file" not in app.agent.allowed_tools


def _request() -> ApprovalRequest:
    loop = asyncio.get_running_loop()
    return ApprovalRequest(
        name="Bash",
        args='echo "hello"',
        reason="default 模式下命令执行需确认",
        respond=loop.create_future(),
    )


@pytest.mark.asyncio
async def test_backspace_deletes_one_chinese_character() -> None:
    app = _make_app()

    async with app.run_test(size=(100, 30)) as pilot:
        chat_input = app.query_one("#chat-input", ChatInput)
        chat_input.text = "你好世界"
        chat_input.cursor_location = (0, len(chat_input.text))
        chat_input.focus()

        await pilot.press("backspace")

        assert chat_input.text == "你好世"

        chat_input.text = "abcd"
        chat_input.cursor_location = (0, len(chat_input.text))
        await pilot.press("backspace")
        assert chat_input.text == "abc"

        chat_input.text = "你好世界"
        selection_type = type(chat_input.selection)
        chat_input.selection = selection_type((0, 1), (0, 3))
        await pilot.press("backspace")
        assert chat_input.text == "你界"


def test_provider_selection_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app()
    app.provider = MagicMock()
    app.agent = MagicMock()
    factory = MagicMock(side_effect=AssertionError("must not reinitialize"))
    monkeypatch.setattr("novacode.tui.app.new_provider", factory)

    app._select_provider(app.providers[0])

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_selected_provider_is_bound_to_team_manager(monkeypatch):
    app = _make_app()
    app.team_mgr = MagicMock()
    provider = MagicMock(model="gpt-4")
    monkeypatch.setattr("novacode.tui.app.new_provider", lambda config: provider)
    async with app.run_test():
        app.team_mgr.bind_provider.assert_called_once_with(provider, app.providers[0])


@pytest.mark.asyncio
async def test_subagent_hook_completion_is_visible_and_injected_next_turn(tmp_path: Path) -> None:
    class Provider:
        name = "hook-provider"
        model = "hook-provider"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def stream(self, request):
            self.started.set()
            await self.release.wait()
            yield StreamEvent(text="background review")
            yield StreamEvent(done=True)

        async def close(self) -> None:
            pass

    cfg = ProviderConfig(
        name="test",
        protocol="openai",
        api_key="sk-test",
        base_url="http://localhost:8000",
        model="gpt-4",
    )
    hook_engine = HookEngine(
        [HookRule("review-stop", HookEvent.STOP, SubagentAction("Plan", "review"))],
        [],
    )
    app = NovaCodeApp(
        providers=[cfg],
        registry=Registry(),
        version="test",
        engine=_make_engine(),
        hook_engine=hook_engine,
        project_root=tmp_path,
        subagent_catalog=load_catalog(tmp_path),
    )
    app._show_system = MagicMock()
    provider = Provider()

    assert app._initialize_provider(cfg, provider) is True
    before = app.conv.messages()
    await hook_engine.dispatch(HookEvent.STOP, {"session_id": "s1"})
    await asyncio.wait_for(provider.started.wait(), timeout=0.2)
    provider.release.set()
    for _ in range(100):
        if app.agent is not None and app.agent.runtime.pending_reminders:
            break
        await asyncio.sleep(0.001)

    notice = app.agent.runtime.pending_reminders[0]
    assert 'source_event="Stop"' in notice
    app._show_system.assert_called_once_with(notice)
    assert app.conv.messages() == before
    await hook_engine.close()
    await app._shutdown_resources()


@pytest.mark.asyncio
async def test_app_shutdown_cancels_and_waits_for_subagent_hooks(tmp_path: Path) -> None:
    class Provider:
        name = "hook-provider"
        model = "hook-provider"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.closed = False

        async def stream(self, request):
            self.started.set()
            await asyncio.Event().wait()
            if False:
                yield StreamEvent()

        async def close(self) -> None:
            self.closed = True

    cfg = ProviderConfig(
        name="test",
        protocol="openai",
        api_key="sk-test",
        base_url="http://localhost:8000",
        model="gpt-4",
    )
    hook_engine = HookEngine(
        [HookRule("review-stop", HookEvent.STOP, SubagentAction("Plan", "review"))],
        [],
    )
    app = NovaCodeApp(
        providers=[cfg],
        registry=Registry(),
        version="test",
        engine=_make_engine(),
        hook_engine=hook_engine,
        project_root=tmp_path,
        subagent_catalog=load_catalog(tmp_path),
    )
    app._show_system = MagicMock()
    provider = Provider()

    assert app._initialize_provider(cfg, provider) is True
    await hook_engine.dispatch(HookEvent.STOP, {"session_id": "s1"})
    await asyncio.wait_for(provider.started.wait(), timeout=0.2)
    try:
        await asyncio.wait_for(app._shutdown_resources(), timeout=0.2)
        assert provider.closed is True
        assert any(
            'status="cancelled"' in reminder for reminder in app.agent.runtime.pending_reminders
        )
    finally:
        await hook_engine.close()


# ── unit: outcome index ───────────────────────────────────────


class TestOutcomeIndex:
    def test_0_allow_once(self):
        assert _outcome_for_index(0) == Outcome.ALLOW_ONCE

    def test_1_allow_forever(self):
        assert _outcome_for_index(1) == Outcome.ALLOW_FOREVER

    def test_2_deny_once(self):
        assert _outcome_for_index(2) == Outcome.DENY_ONCE


@pytest.mark.asyncio
async def test_dispatch_slash_known_unknown_and_non_command() -> None:
    app = _make_app()
    app._show_system = MagicMock()

    assert await app.dispatch_slash("hello") is False
    assert await app.dispatch_slash("/missing") is True
    app._show_system.assert_called_with("未知命令：输入 /help 查看可用命令")

    app._show_system.reset_mock()
    assert await app.dispatch_slash("/Help") is True
    help_text = app._show_system.call_args.args[0]
    assert len(help_text.splitlines()) == 16
    assert "/hooks" in help_text
    assert "/skill" in help_text


@pytest.mark.asyncio
async def test_dispatch_plan_is_local_and_do_injects() -> None:
    app = _make_app()
    app._show_system = MagicMock()
    before = app.conv.messages()

    assert await app.dispatch_slash("/plan") is True
    assert app.mode() == Mode.PLAN
    assert app.conv.messages() == before

    app._dispatch = AsyncMock()
    assert await app.dispatch_slash("/do") is True
    assert app.mode() == Mode.DEFAULT
    app._dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_rejects_ui_command_while_busy() -> None:
    app = _make_app()
    app.state = SessionState.STREAMING
    app.error = MagicMock()

    assert await app.dispatch_slash("/plan") is True

    app.error.assert_called_once_with("请等待当前任务完成")
    assert app.mode() == Mode.BYPASS


@pytest.mark.asyncio
async def test_submit_allows_local_command_while_busy() -> None:
    app = _make_app()
    app.provider = MagicMock(model="test-model")
    app.state = SessionState.STREAMING
    app._show_system = MagicMock()

    await app._on_submit(ChatInput.Submitted("/permission"))

    app._show_system.assert_called_once_with("bypassPermissions")


@pytest.mark.asyncio
async def test_review_persists_prompt_and_starts_turn(tmp_path: Path) -> None:
    session = SessionService.create(tmp_path)
    app = _make_app(session, tmp_path)

    async with app.run_test(size=(100, 30)):
        app._start_stream = AsyncMock()
        assert await app.dispatch_slash("/review") is True

        assert app.conv.messages()[-1].content == REVIEW_DIRECTIVE
        assert load_session(session.path).messages[-1].content == REVIEW_DIRECTIVE
        app._start_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_starts_new_persistent_session_and_resets_usage(tmp_path: Path) -> None:
    app, service = _resume_app(tmp_path)
    old_context = app.session_context
    old_path = service.path
    chat = MagicMock()
    chat.remove_children = MagicMock(return_value=asyncio.sleep(0))
    app.query_one = MagicMock(return_value=chat)
    app.println = MagicMock()
    app.error = MagicMock()
    app._usage_in = 100
    app._usage_out = 20
    app.conv.add_user("old")
    app.agent.activate_skill("old-skill", "old SOP")

    await app.clear_and_new_session()

    assert app.session_context is not old_context
    assert app.session_context.session_id != old_context.session_id
    assert service.path != old_path
    assert app.conv.messages() == []
    assert app.agent.active_skills == {}
    assert app.usage_in() == app.usage_out() == 0
    assert old_path.exists()
    app.println.assert_called_once_with("已清空当前会话，开启新 session")
    await service.close()


def _resume_app(tmp_path: Path) -> tuple[NovaCodeApp, SessionService]:
    service = SessionService.create(tmp_path, "model-a")
    current = service.context
    assert current is not None
    app = _make_app(service, tmp_path)

    class Provider:
        name = "fake"
        model = "model-a"

        async def stream(self, request):
            if False:
                yield None

    app.provider = Provider()
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=current,
    )
    app.agent = Agent(app.provider, app._tool_registry, runtime=runtime)
    service.bind_agent(app.agent, app._current_tool_defs)
    app.state = SessionState.IDLE
    app._show_system = MagicMock()
    return app, service


def _target_session(tmp_path: Path, content: str = "restored"):
    context = new_session_context(str(tmp_path))
    writer = SessionWriter(Path(context.message_path).parent, context.session_id, "model-a")
    writer.append_message(Message(role="user", content=content))
    writer.close()
    return next(
        info
        for info in list_sessions(Path(context.message_path).parent)
        if info.session_id == context.session_id
    )


def test_format_session_option_contains_recovery_metadata(tmp_path: Path) -> None:
    info = _target_session(tmp_path)

    label = format_session_option(info)

    assert info.session_id in label
    assert info.title in label
    assert info.model in label


@pytest.mark.asyncio
async def test_resume_session_atomically_switches_and_continues_original_jsonl(
    tmp_path: Path,
) -> None:
    app, service = _resume_app(tmp_path)
    old_context = app.session_context
    info = _target_session(tmp_path)

    assert await app.resume_session(info) is True

    assert app.session_context.session_id == info.session_id
    assert service.path == info.path
    assert [message.content for message in app.conv.messages()] == ["restored"]
    app.conv.add_assistant("continued")
    await service.sync()
    assert [message.content for message in load_session(info.path).messages] == [
        "restored",
        "continued",
    ]
    assert old_context.session_id != app.session_context.session_id


@pytest.mark.asyncio
async def test_resume_stale_session_sets_ephemeral_reminder_without_rewriting_file(
    tmp_path: Path,
) -> None:
    app, _ = _resume_app(tmp_path)
    info = _target_session(tmp_path)
    record = load_session(info.path)
    raw = info.path.read_text(encoding="utf-8")
    data = __import__("json").loads(raw)
    data["ts"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    info.path.write_text(__import__("json").dumps(data) + "\n", encoding="utf-8")
    info = next(
        item for item in list_sessions(info.path.parent) if item.session_id == info.session_id
    )
    before = info.path.read_bytes()

    assert record.messages and await app.resume_session(info) is True

    assert app.agent.runtime.resume_reminder
    assert "可能已经过期" in app.agent.runtime.resume_reminder
    assert info.path.read_bytes() == before


@pytest.mark.asyncio
async def test_resume_read_failure_keeps_current_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, service = _resume_app(tmp_path)
    old_context = app.session_context
    old_conv = app.conv
    old_path = service.path
    info = _target_session(tmp_path)
    monkeypatch.setattr(
        "novacode.session.service.load_session",
        MagicMock(side_effect=OSError("cannot read session")),
    )

    assert await app.resume_session(info) is False

    assert app.session_context is old_context
    assert app.conv is old_conv
    assert service.path == old_path
    await service.record(Message(role="user", content="still writable"))
    assert app.state is SessionState.IDLE
    assert "cannot read session" in app._show_system.call_args.args[0]


@pytest.mark.asyncio
async def test_startup_warning_is_shown_in_tui() -> None:
    app = _make_app()
    warning = "权限配置已回退到内建默认策略：settings.yaml: invalid YAML"
    app._pending_background_notices.append(warning)
    app._show_system = MagicMock()

    async with app.run_test(size=(100, 30)):
        app._show_system.assert_any_call(warning)


def test_format_compact_notice_reports_growth_without_saying_drop() -> None:
    text = format_compact_notice(CompactPhase.AFTER_AUTO, 100, 150, None)

    assert "100" in text
    assert "150" in text
    assert "降至" not in text


# ── unit: _update_approving key dispatch ──────────────────────


class TestUpdateApproving:
    """Test the pure key→outcome dispatch logic without TUI rendering."""

    @pytest.mark.asyncio
    async def test_enter_commits_default_cursor_allow_once(self):
        """enter at cursor 0 → ALLOW_ONCE."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("enter")
        assert handled is True
        assert app.pending is None
        assert app.approve_cursor == 0
        assert req.respond.done()
        assert req.respond.result() == Outcome.ALLOW_ONCE
        assert app.state == SessionState.STREAMING

    @pytest.mark.asyncio
    async def test_down_then_enter_commits_allow_forever(self):
        """down moves to 1, enter → ALLOW_FOREVER."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        app._update_approving("down")  # cursor → 1
        assert app.approve_cursor == 1

        handled = app._update_approving("enter")
        assert handled is True
        assert req.respond.result() == Outcome.ALLOW_FOREVER

    @pytest.mark.asyncio
    async def test_key_3_deny_once(self):
        """3 → DENY_ONCE directly."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("3")
        assert handled is True
        assert req.respond.result() == Outcome.DENY_ONCE

    @pytest.mark.asyncio
    async def test_key_y_allow_once(self):
        """y → ALLOW_ONCE."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("y")
        assert handled is True
        assert req.respond.result() == Outcome.ALLOW_ONCE

    @pytest.mark.asyncio
    async def test_key_n_deny_once(self):
        """n → DENY_ONCE."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("n")
        assert handled is True
        assert req.respond.result() == Outcome.DENY_ONCE

    @pytest.mark.asyncio
    async def test_key_d_deny_once(self):
        """d → DENY_ONCE."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("d")
        assert handled is True
        assert req.respond.result() == Outcome.DENY_ONCE

    @pytest.mark.asyncio
    async def test_key_1_allow_once(self):
        """1 → ALLOW_ONCE."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("1")
        assert handled is True
        assert req.respond.result() == Outcome.ALLOW_ONCE

    @pytest.mark.asyncio
    async def test_key_2_allow_forever(self):
        """2 → ALLOW_FOREVER."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("2")
        assert handled is True
        assert req.respond.result() == Outcome.ALLOW_FOREVER

    @pytest.mark.asyncio
    async def test_space_commits_current_cursor(self):
        """space → commits cursor item."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 2
        app.state = SessionState.APPROVING

        handled = app._update_approving("space")
        assert handled is True
        assert req.respond.result() == Outcome.DENY_ONCE

    @pytest.mark.asyncio
    async def test_up_wraps_to_bottom(self):
        """up at 0 wraps to 2."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        app._update_approving("up")
        assert app.approve_cursor == 2

    @pytest.mark.asyncio
    async def test_down_wraps_to_top(self):
        """down at 2 wraps to 0."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 2
        app.state = SessionState.APPROVING

        app._update_approving("down")
        assert app.approve_cursor == 0

    @pytest.mark.asyncio
    async def test_j_and_k_aliases(self):
        """j = down, k = up (vim keys)."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        app._update_approving("j")
        assert app.approve_cursor == 1
        app._update_approving("k")
        assert app.approve_cursor == 0

    @pytest.mark.asyncio
    async def test_unhandled_key_returns_false(self):
        """Random key → False, state unchanged."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.approve_cursor = 0
        app.state = SessionState.APPROVING

        handled = app._update_approving("x")
        assert handled is False
        assert app.pending is not None  # still pending
        assert app.state == SessionState.APPROVING

    @pytest.mark.asyncio
    async def test_commit_clears_approval_widget_ref(self):
        """After commit, _approval_widget is None."""
        app = _make_app()
        req = _request()
        app.pending = req
        app.state = SessionState.APPROVING
        app._approval_widget = MagicMock()

        app._commit_approval(Outcome.ALLOW_ONCE)
        assert app._approval_widget is None

    @pytest.mark.asyncio
    async def test_enter_without_pending_noop(self):
        """No pending → enter is no-op."""
        app = _make_app()
        app.state = SessionState.APPROVING
        handled = app._update_approving("enter")
        assert handled is True
        # No crash, no side effect


# ── note: ChatInput._on_key delegation requires Textual App mount ──
# ── tested via end-to-end tmux session (see plan verification)  ──
# ── unit: _commit_approval state transitions ─────────────────


class TestCommitApproval:
    @pytest.mark.asyncio
    async def test_resets_state_to_streaming(self):
        app = _make_app()
        req = _request()
        app.pending = req
        app.state = SessionState.APPROVING

        app._commit_approval(Outcome.ALLOW_ONCE)
        assert app.state == SessionState.STREAMING
        assert app.pending is None
        assert app.approve_cursor == 0

    @pytest.mark.asyncio
    async def test_noop_when_pending_is_none(self):
        app = _make_app()
        app.pending = None
        app._commit_approval(Outcome.ALLOW_ONCE)
        # No crash.

    @pytest.mark.asyncio
    async def test_no_double_resolve(self):
        app = _make_app()
        req = _request()
        app.pending = req

        app._commit_approval(Outcome.ALLOW_ONCE)
        # Second call is noop
        app._commit_approval(Outcome.DENY_ONCE)

        assert req.respond.result() == Outcome.ALLOW_ONCE  # first value sticks


class TestTurnCancellation:
    @pytest.mark.asyncio
    async def test_escape_while_approving_sets_cancel_and_denies(self):
        app = _make_app()
        req = _request()
        app.pending = req
        app.turn_cancel = asyncio.Event()
        app.state = SessionState.APPROVING

        app.action_cancel()

        assert app.turn_cancel.is_set()
        assert req.respond.result() == Outcome.DENY_ONCE

    @pytest.mark.asyncio
    async def test_ctrl_c_sets_cancel_without_cancelling_consumer_task(self):
        app = _make_app()
        app.state = SessionState.STREAMING
        app.turn_cancel = asyncio.Event()
        app._agent_task = asyncio.create_task(asyncio.sleep(10))
        app._show_system = MagicMock()
        app._finish_streaming = MagicMock()

        await app.action_handle_ctrl_c()

        assert app.turn_cancel.is_set()
        assert not app._agent_task.cancelled()
        app._finish_streaming.assert_not_called()
        app._agent_task.cancel()

    @pytest.mark.asyncio
    async def test_second_ctrl_c_force_cancels_stuck_approval_turn(self):
        """第一次优雅取消审批，若未退栈，第二次必须强制结束当前 turn。"""
        app = _make_app()
        request = _request()
        app.pending = request
        app.state = SessionState.APPROVING
        app.turn_cancel = asyncio.Event()
        app._agent_task = asyncio.create_task(asyncio.Event().wait())
        app._finish_streaming = MagicMock(
            side_effect=lambda: setattr(app, "state", SessionState.IDLE)
        )

        await app.action_handle_ctrl_c()
        assert app.turn_cancel.is_set()
        assert request.respond.result() == Outcome.DENY_ONCE
        assert app.pending is None
        assert app.state == SessionState.STREAMING

        await app.action_handle_ctrl_c()
        await asyncio.sleep(0)
        assert app._agent_task.cancelled()
        assert app.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_consumer_natural_cancel_end_returns_to_idle_once(self):
        app = _make_app()
        app.state = SessionState.STREAMING
        app.turn_cancel = asyncio.Event()
        app.turn_cancel.set()
        app._show_system = MagicMock()
        app._finish_streaming = MagicMock(
            side_effect=lambda: setattr(app, "state", SessionState.IDLE)
        )

        async def empty_events():
            if False:
                yield None

        await app._consume_events(empty_events())

        app._show_system.assert_called_once_with("(response interrupted)")
        app._finish_streaming.assert_called_once()
        assert app.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_provider_resources_bind_writer_then_extractor_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app()
    order: list[str] = []

    class Extractor:
        def __init__(self):
            self.closed = asyncio.Event()

        def bind_provider(self, provider):
            order.append("extractor")

        async def run(self):
            await self.closed.wait()

        async def close(self):
            order.append("extractor-close")
            self.closed.set()

    provider = MagicMock(model="gpt-4")
    app.session.bind_model = MagicMock(side_effect=lambda model: order.append(f"session:{model}"))
    app.extractor = Extractor()

    def fake_agent(*args, **kwargs):
        assert app._extractor_task is not None
        order.append("agent")
        return MagicMock()

    monkeypatch.setattr("novacode.tui.app.Agent", fake_agent)

    assert app._initialize_provider(app.providers[0], provider) is True
    assert order == ["session:gpt-4", "extractor", "agent"]
    await app._shutdown_resources()


@pytest.mark.asyncio
async def test_app_shares_provider_with_memory_borrowers_and_closes_it_once() -> None:
    app = _make_app()

    class Provider:
        name = "fake"
        model = "gpt-4"

        def __init__(self) -> None:
            self.close_calls = 0

        async def stream(self, request):
            if False:
                yield None

        async def close(self) -> None:
            self.close_calls += 1

    class Extractor:
        def __init__(self) -> None:
            self.provider = None
            self.stopped = asyncio.Event()

        def bind_provider(self, provider) -> None:
            self.provider = provider

        async def run(self) -> None:
            await self.stopped.wait()

        async def close(self) -> None:
            self.stopped.set()

    class Governor:
        def __init__(self) -> None:
            self.provider = None

        def bind_provider(self, provider) -> None:
            self.provider = provider

        async def close(self) -> None:
            return None

    class Hook:
        def __init__(self) -> None:
            self.provider = None

        def bind_subagent_runtime(self, parent, catalog, notify) -> None:
            self.provider = parent.provider

        async def close(self) -> None:
            return None

    provider = Provider()
    app.extractor = Extractor()
    app.governor = Governor()
    app.hook_engine = Hook()

    assert app._initialize_provider(app.providers[0], provider) is True
    assert app.agent.provider is provider
    assert app.extractor.provider is provider
    assert app.governor.provider is provider
    assert app.hook_engine.provider is provider

    await app._shutdown_resources()
    await app._shutdown_resources()
    assert provider.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_exit_waits_for_memory_and_second_ctrl_c_cancels_queue(tmp_path, force) -> None:
    app = _make_app(project_root=tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        name = "fake"
        model = "gpt-4"

        def __init__(self):
            self.close_calls = 0
            self.requests = 0

        async def stream(self, request):
            self.requests += 1
            started.set()
            await release.wait()
            yield StreamEvent(
                text='[{"action":"create","kind":"project",'
                '"title":"Durable","summary":"ok","content":"ok"}]',
                done=True,
            )

        async def close(self):
            self.close_calls += 1

    provider = Provider()
    user = MemoryStore(tmp_path / "user", frozenset({MemoryKind.USER}))
    project = MemoryStore(tmp_path / "project", frozenset({MemoryKind.PROJECT}))
    app.extractor = MemoryExtractor(user, project, lambda _: None)
    app.governor = MemoryGovernor(
        tmp_path / "sessions", (user, project), AsyncMock(return_value=[]), lambda _: None
    )
    assert app._initialize_provider(app.providers[0], provider)

    async with app.run_test() as pilot:
        # 使用真正的按键消息循环，确保第一次退出没有堵塞第二次按键。
        app.extractor.submit(MemoryTurn("one", "answer"))
        app.extractor.submit(MemoryTurn("two", "answer"))
        await asyncio.wait_for(started.wait(), 1)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._exit_task is not None and not app._exit_task.done()
        assert provider.close_calls == 0
        with pytest.raises(RuntimeError, match="closed"):
            app.extractor.submit(MemoryTurn("late", "answer"))
        notices = " ".join(str(widget.render()) for widget in app.query(".system-message"))
        assert "正在等待记忆提取完成" in notices
        if force:
            await pilot.press("ctrl+c")
        else:
            release.set()
        await asyncio.wait_for(app._exit_task, 2)

    assert app.extractor.pending == 0
    assert app.extractor.provider is None
    assert app.governor.provider is provider
    assert provider.close_calls == 1
    assert provider.requests == (1 if force else 2)
    assert (project.directory / "MEMORY.md").exists() is (not force)
    await app._shutdown_resources()
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_writer_bind_failure_keeps_agent_unavailable_and_does_not_bind_extractor() -> None:
    app = _make_app()

    extractor = MagicMock()
    app.session.bind_model = MagicMock(side_effect=SessionWriteError("cannot bind"))
    app.extractor = extractor

    assert app._initialize_provider(app.providers[0], MagicMock(model="gpt-4")) is False
    assert app.agent is None
    extractor.bind_provider.assert_not_called()


@pytest.mark.asyncio
async def test_done_event_restores_input_before_nonblocking_memory_submit() -> None:
    app = _make_app()
    states: list[SessionState] = []
    turn = MemoryTurn("question", "answer")

    class Extractor:
        def submit(self, submitted):
            states.append(app.state)
            assert submitted == turn

    app.extractor = Extractor()
    app.state = SessionState.STREAMING
    app._finish_with_assistant = MagicMock(
        side_effect=lambda _: setattr(app, "state", SessionState.IDLE)
    )

    async def events():
        yield Event(done=True, memory_turn=turn)

    await app._consume_events(events())

    app._finish_with_assistant.assert_called_once_with("")
    assert states == [SessionState.IDLE]


@pytest.mark.asyncio
async def test_main_and_subagent_approvals_are_displayed_in_fifo_order() -> None:
    """主流程和后台 SubAgent 的审批不能互相覆盖。"""
    app = _make_app()
    app._mount_approval_block = MagicMock()
    background_first = _request()
    main_second = _request()
    background_third = _request()
    consumer = asyncio.create_task(app._consume_task_approvals())

    async def wait_until_pending(request: ApprovalRequest) -> None:
        for _ in range(100):
            if app.pending is request:
                return
            await asyncio.sleep(0.001)
        raise AssertionError(f"审批请求未按顺序显示: {request.name}")

    try:
        await app.task_mgr.subscribe_approvals().put(background_first)
        await wait_until_pending(background_first)

        async def main_events():
            yield Event(approval=main_second)

        await app._consume_events(main_events())
        await app.task_mgr.subscribe_approvals().put(background_third)

        assert app.pending is background_first
        assert not main_second.respond.done()
        assert not background_third.respond.done()

        app._commit_approval(Outcome.ALLOW_ONCE)
        await wait_until_pending(main_second)
        assert background_first.respond.result() is Outcome.ALLOW_ONCE
        assert not background_third.respond.done()

        app._commit_approval(Outcome.ALLOW_ONCE)
        await wait_until_pending(background_third)
        assert main_second.respond.result() is Outcome.ALLOW_ONCE

        app._commit_approval(Outcome.ALLOW_ONCE)
        assert background_third.respond.result() is Outcome.ALLOW_ONCE
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_user_persistence_failure_does_not_render_or_start_agent() -> None:
    app = _make_app()
    app.session.record = AsyncMock(side_effect=OSError("disk"))
    app._show_system = MagicMock()
    app._start_stream = MagicMock()

    await app._dispatch("must persist")

    app._start_stream.assert_not_called()
    app._show_system.assert_called_once()
