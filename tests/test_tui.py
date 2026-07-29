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
from novacode.conversation import Conversation
from novacode.llm import Message, ToolResult
from novacode.memory import MemoryTurn
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.rule import RuleSet
from novacode.session import SessionWriteError, SessionWriter, list_sessions, load_session
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


def _make_app() -> NovaCodeApp:
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
    )


def _request() -> ApprovalRequest:
    loop = asyncio.get_running_loop()
    return ApprovalRequest(
        name="Bash",
        args='echo "hello"',
        reason="default 模式下命令执行需确认",
        respond=loop.create_future(),
    )


def test_provider_selection_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app()
    app.provider = MagicMock()
    app.agent = MagicMock()
    factory = MagicMock(side_effect=AssertionError("must not reinitialize"))
    monkeypatch.setattr("novacode.tui.app.new_provider", factory)

    app._select_provider(app.providers[0])

    factory.assert_not_called()


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
    assert len(help_text.splitlines()) == 14
    assert "/hooks" in help_text
    assert "/skill" in help_text


@pytest.mark.asyncio
async def test_dispatch_plan_is_local_and_do_injects() -> None:
    app = _make_app()
    app._show_system = MagicMock()
    app.conv = MagicMock()

    assert await app.dispatch_slash("/plan") is True
    assert app.mode() == Mode.PLAN
    app.conv.add_user.assert_not_called()

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
    context = new_session_context(str(tmp_path))
    writer = SessionWriter(Path(context.message_path).parent, context.session_id, "")
    app = _make_app()
    app.session_context = context
    app.writer = writer
    app.conv = Conversation(writer.append_message, writer.append_compaction)

    async with app.run_test(size=(100, 30)):
        app._start_stream = AsyncMock()
        assert await app.dispatch_slash("/review") is True

        assert app.conv.messages()[-1].content == REVIEW_DIRECTIVE
        assert load_session(writer.path).messages[-1].content == REVIEW_DIRECTIVE
        app._start_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_starts_new_persistent_session_and_resets_usage(tmp_path: Path) -> None:
    app, old_writer = _resume_app(tmp_path)
    old_context = app.session_context
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
    assert app.writer is not old_writer
    assert app.conv.messages() == []
    assert app.agent.active_skills == {}
    assert app.usage_in() == app.usage_out() == 0
    assert old_writer.path.exists()
    app.println.assert_called_once_with("已清空当前会话，开启新 session")
    app.writer.close()


def _resume_app(tmp_path: Path) -> tuple[NovaCodeApp, SessionWriter]:
    app = _make_app()
    current = new_session_context(str(tmp_path))
    writer = SessionWriter(Path(current.message_path).parent, current.session_id, "model-a")
    app.project_root = tmp_path
    app.session_context = current
    app.writer = writer
    app.conv = Conversation(writer.append_message, writer.append_compaction)

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
    app.state = SessionState.IDLE
    app._show_system = MagicMock()
    return app, writer


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
    app, old_writer = _resume_app(tmp_path)
    old_context = app.session_context
    info = _target_session(tmp_path)

    assert await app.resume_session(info) is True

    assert app.session_context.session_id == info.session_id
    assert app.writer.path == info.path
    assert [message.content for message in app.conv.messages()] == ["restored"]
    app.conv.add_assistant("continued")
    assert [message.content for message in load_session(info.path).messages] == [
        "restored",
        "continued",
    ]
    with pytest.raises(SessionWriteError, match="closed"):
        old_writer.append_message(Message(role="user", content="late"))
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


def test_promote_staged_spills_avoids_overwrite_and_rewrites_candidate(tmp_path: Path) -> None:
    app, _ = _resume_app(tmp_path)
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    target.mkdir()
    source = staging / "tool"
    source.write_text("new", encoding="utf-8")
    (target / "tool").write_text("existing", encoding="utf-8")
    candidate = Conversation.from_messages(
        [Message(role="tool", tool_results=[ToolResult("t1", f"[saved to] {source}")])]
    )

    promoted = app._promote_staged_spills(candidate, staging, target)

    assert (target / "tool").read_text(encoding="utf-8") == "existing"
    assert len(promoted) == 1 and promoted[0].read_text(encoding="utf-8") == "new"
    assert str(promoted[0]) in candidate.messages()[0].tool_results[0].content
    assert str(source) not in candidate.messages()[0].tool_results[0].content


@pytest.mark.asyncio
async def test_resume_compaction_failure_before_commit_cleans_promoted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _resume_app(tmp_path)
    old_context = app.session_context
    old_conv = app.conv
    info = _target_session(tmp_path)

    monkeypatch.setattr(app, "_needs_resume_compaction", lambda candidate: True)

    async def fake_compact(candidate, runtime):
        Path(runtime.session.spill_dir).mkdir(parents=True, exist_ok=True)
        (Path(runtime.session.spill_dir) / "artifact").write_text("new", encoding="utf-8")

    monkeypatch.setattr(app, "_compact_resume_candidate", fake_compact)
    monkeypatch.setattr(
        SessionWriter,
        "append_compaction",
        lambda self, replacement: (_ for _ in ()).throw(SessionWriteError("commit failed")),
    )

    assert await app.resume_session(info) is False

    assert app.session_context is old_context and app.conv is old_conv
    target_tools = info.path.parent / info.session_id / "tool-results"
    assert list(target_tools.glob("*")) == []
    assert list(info.path.parent.glob(".resume-staging-*")) == []


@pytest.mark.asyncio
async def test_resume_switch_failure_after_commit_keeps_promoted_files_and_old_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _resume_app(tmp_path)
    old_context = app.session_context
    old_conv = app.conv
    info = _target_session(tmp_path)
    monkeypatch.setattr(app, "_needs_resume_compaction", lambda candidate: True)

    async def fake_compact(candidate, runtime):
        Path(runtime.session.spill_dir).mkdir(parents=True, exist_ok=True)
        (Path(runtime.session.spill_dir) / "artifact").write_text("new", encoding="utf-8")

    monkeypatch.setattr(app, "_compact_resume_candidate", fake_compact)
    monkeypatch.setattr(
        app,
        "_swap_session",
        lambda *args: (_ for _ in ()).throw(RuntimeError("switch failed")),
    )

    assert await app.resume_session(info) is False

    assert app.session_context is old_context and app.conv is old_conv
    target_tools = info.path.parent / info.session_id / "tool-results"
    assert [path.read_text(encoding="utf-8") for path in target_tools.iterdir()] == ["new"]
    assert [message.content for message in load_session(info.path).messages] == ["restored"]
    assert list(info.path.parent.glob(".resume-staging-*")) == []


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

    class Writer:
        def bind_model(self, model):
            order.append(f"writer:{model}")

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
    app.writer = Writer()
    app.extractor = Extractor()

    def fake_agent(*args, **kwargs):
        assert app._extractor_task is not None
        order.append("agent")
        return MagicMock()

    monkeypatch.setattr("novacode.tui.app.Agent", fake_agent)

    assert app._initialize_provider(app.providers[0], provider) is True
    assert order == ["writer:gpt-4", "extractor", "agent"]
    await app._shutdown_resources()


@pytest.mark.asyncio
async def test_writer_bind_failure_keeps_agent_unavailable_and_does_not_bind_extractor() -> None:
    app = _make_app()

    class Writer:
        def bind_model(self, model):
            raise SessionWriteError("cannot bind")

    extractor = MagicMock()
    app.writer = Writer()
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
async def test_user_persistence_failure_does_not_render_or_start_agent() -> None:
    app = _make_app()
    app.conv = Conversation(before_append=lambda _: (_ for _ in ()).throw(OSError("disk")))
    app._show_system = MagicMock()
    app._start_stream = MagicMock()

    await app._dispatch("must persist")

    app._start_stream.assert_not_called()
    app._show_system.assert_called_once()
