"""Textual TUI application — NovaCodeApp."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.timer import Timer
from textual.widgets import Markdown, OptionList, Static, TextArea

from novacode import __version__
from novacode.agent import Agent, ApprovalRequest, CompactPhase, Phase
from novacode.assembly import assemble_agent
from novacode.command import Kind, arguments, parse, register_builtins
from novacode.command import Registry as CommandRegistry
from novacode.command.builtin_skill import register_skill_management
from novacode.command.skill_register import register_skill_commands
from novacode.config import ProviderConfig
from novacode.hook import Engine as HookEngine
from novacode.hook import Event as HookEvent
from novacode.llm import Message, new_provider
from novacode.llm import Provider as LLMProvider
from novacode.memory import MemoryExtractor, MemoryGovernor
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.session import SessionInfo, SessionService, list_sessions
from novacode.skills import SkillExecutor, SkillLoader
from novacode.subagent import Catalog as SubAgentCatalog
from novacode.subagent import load_catalog as load_subagent_catalog
from novacode.task import AgentRunManager
from novacode.tool import Registry as ToolRegistry
from novacode.tool import with_cwd
from novacode.tool.install_skill import InstallSkillTool
from novacode.tool.load_skill import LoadSkill
from novacode.tui.commands import format_compact_notice
from novacode.tui.complete import CompletionMenu
from novacode.tui.resume import build_resume_options
from novacode.tui.tasks import build_task_notification, build_team_update_reminder
from novacode.tui.view import approval_block, tool_line, tool_result_summary
from novacode.worktree import Manager as WorktreeManager

if TYPE_CHECKING:
    from novacode.compact import SessionContext
    from novacode.conversation import Conversation

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
logger = logging.getLogger(__name__)


@dataclass
class ToolDisplay:
    name: str
    args: str


class SessionState(Enum):
    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"
    RESUMING = "resuming"


class ChatInput(TextArea):
    """TextArea: Enter submits, Shift+Enter inserts newline."""

    class Submitted(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        # 审批态：按键委托给 App._update_approving，防止被 TextArea 吞掉
        if self.app.state == SessionState.APPROVING:
            self.app._update_approving(event.key)
            event.stop()
            event.prevent_default()
            return
        if self.app.state == SessionState.IDLE and self.app._handle_completion_key(event):
            return
        if event.key == "escape":
            self.app.action_cancel()
            event.stop()
            event.prevent_default()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if "shift" in getattr(event, "modifiers", ""):
                self.insert("\n")
            else:
                if self.text.strip():
                    self.post_message(self.Submitted(self.text))
                self.clear()
            return


def _next_mode(m: Mode) -> Mode:
    """循环切换四档模式：DEFAULT → ACCEPT_EDITS → PLAN → BYPASS → DEFAULT。"""
    return Mode((int(m) + 1) % 4)


def _outcome_for_index(idx: int) -> Outcome:
    """菜单索引 → Outcome。0=ALLOW_ONCE, 1=ALLOW_FOREVER, 2=DENY_ONCE。"""
    return [Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE][idx]


class NovaCodeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "NovaCode"

    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Ctrl+C", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: ToolRegistry,
        version: str | None = None,
        driver_class: type | None = None,
        engine: Engine | None = None,
        hook_engine: HookEngine | None = None,
        project_root: Path | None = None,
        session: SessionService | None = None,
        extractor: MemoryExtractor | None = None,
        governor: MemoryGovernor | None = None,
        instructions: str = "",
        memory_index: str | Callable[[], str] = "",
        task_mgr: AgentRunManager | None = None,
        subagent_catalog: SubAgentCatalog | None = None,
        worktree_mgr: WorktreeManager | None = None,
        team_mgr=None,
        coordinator_mode: bool = False,
        context_compression: bool = True,
        progressive_tool_schema: bool = False,
        startup_warnings: list[str] | None = None,
        provider_factory: Callable[[ProviderConfig], LLMProvider] | None = None,
        agent_factory: Callable[[LLMProvider, ToolRegistry, SessionService], Agent] | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self._version = version or __version__
        self.providers = providers
        self._provider_factory = provider_factory
        self._agent_factory = agent_factory
        self.provider: LLMProvider | None = None
        self.provider_cfg: ProviderConfig | None = None
        self.agent: Agent | None = None
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.task_mgr = task_mgr or AgentRunManager()
        self.subagent_catalog = subagent_catalog or load_subagent_catalog(self.project_root)
        self.worktree_mgr = worktree_mgr
        self.team_mgr = team_mgr
        self.coordinator_mode = coordinator_mode
        self.context_compression = context_compression
        self.progressive_tool_schema = progressive_tool_schema
        self.lead_mail_event = asyncio.Event()
        worktree_session = worktree_mgr.current_session() if worktree_mgr is not None else None
        self.active_cwd = worktree_session.worktree_path if worktree_session is not None else ""
        self._worktree_adapter = None
        self._task_consumers: list[asyncio.Task] = []
        self.session = session or SessionService.ephemeral(self.project_root)
        self.session.bind_lifecycle(self._dispatch_session_hook)
        self.extractor = extractor
        self.governor = governor
        self.instructions = instructions
        self._memory_index = memory_index if callable(memory_index) else lambda: memory_index
        self._extractor_task: asyncio.Task[None] | None = None
        self.cleanup_task: asyncio.Task | None = None
        self._shutdown_started = False
        self._exit_task: asyncio.Task[None] | None = None
        self._pending_background_notices = list(startup_warnings or [])
        self._tool_registry = registry
        self.skill_loader = SkillLoader(self.project_root)
        self.skill_loader.load_all()
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool = LoadSkill()
        self._load_skill_tool.set_loader(self.skill_loader)
        self._tool_registry.register(self._load_skill_tool)
        self._install_skill_tool = InstallSkillTool(
            self.skill_loader,
            Path.home() / ".novacode" / "skills",
            self._sync_skills,
        )
        self._tool_registry.register(self._install_skill_tool)
        self.cmd_registry = CommandRegistry()
        register_builtins(self.cmd_registry)
        register_skill_management(self.cmd_registry, self.skill_loader, self._sync_skills)
        self._command_args = ""
        self.completion = CompletionMenu()
        self.engine = engine
        self.hook_engine = hook_engine
        self.state = SessionState.SELECTING if len(providers) > 1 else SessionState.IDLE
        self.turn_start = 0.0
        self._agent_task: asyncio.Task[None] | None = None
        self._mode: Mode = engine.start_mode if engine else Mode.DEFAULT
        self.iter: int = 0
        self._usage_in: int = 0
        self._usage_out: int = 0
        self.cur_tools: list[ToolDisplay] = []
        self.turn_cancel: asyncio.Event | None = None
        # 人在回路状态
        self.pending: ApprovalRequest | None = None
        self.approve_cursor: int = 0
        self._approval_widget: Static | None = None
        # 流式渲染状态
        self._current_ai_row: Vertical | None = None
        self._streaming_label: Static | None = None
        self._text_chunks: list[str] = []
        self._stream_dirty = False
        self._stream_timer: Timer | None = None
        self._spinner_label: Static | None = None
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._last_ai_text: str = ""
        self._resume_sessions: dict[str, SessionInfo] = {}
        self._resume_list: OptionList | None = None

    # ── compose ─────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")
        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[f"{p.name}  [{p.model}]" for p in self.providers],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input", disabled=True)
            yield Static("", id="completion-menu")
            with Horizontal(id="status-bar"):
                yield Static("", id="mode-label")
                yield Static("", id="model-label")

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> Text:
        t = Text()
        t.append(" /\\_/\\    ", style="bold #875FFF")
        t.append(f"NovaCode v{__version__}\n", style="#c9d1d9")
        t.append("( o.o )   ", style="bold #875FFF")
        t.append(f"{model}\n" if model else "\n", style="#c9d1d9")
        t.append(" > ^ <    ", style="bold #875FFF")
        t.append(work_dir, style="#c9d1d9")
        return t

    async def on_mount(self) -> None:
        if len(self.providers) == 1:
            self._select_provider(self.providers[0])
            await self.dispatch_session_start()
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

        for notice in self._pending_background_notices:
            self._show_system(notice)
        self._pending_background_notices.clear()
        self._task_consumers = [
            asyncio.create_task(self._consume_task_done()),
            asyncio.create_task(self._consume_task_approvals()),
        ]
        if self.team_mgr is not None:
            self._task_consumers.append(asyncio.create_task(self._consume_lead_mail()))

    async def on_unmount(self) -> None:
        await self._shutdown_resources()

    def notify_background(self, notice: str) -> None:
        try:
            self._show_system(notice)
        except Exception:
            self._pending_background_notices.append(notice)

    def _notify_subagent_hook(self, notice: str) -> None:
        self.notify_background(notice)
        if self.agent is not None:
            self.agent.runtime.append_reminders([notice])

    def _select_provider(self, provider_cfg: ProviderConfig) -> None:
        if self.provider is not None and self.agent is not None:
            return
        try:
            chat_input = self.query_one("#chat-input", ChatInput)
            chat_input.disabled = True
        except Exception:
            chat_input = None
        try:
            provider = (self._provider_factory or new_provider)(provider_cfg)
        except Exception as exc:
            logger.warning("provider creation failed: %s", type(exc).__name__)
            self.state = SessionState.SELECTING
            self._show_system("Provider initialization failed; input remains disabled.")
            return
        if not self._initialize_provider(provider_cfg, provider):
            self.state = SessionState.SELECTING
            self._show_system("Provider initialization failed; input remains disabled.")
            return

        assert self.provider is not None
        if self.team_mgr is not None:
            self.team_mgr.bind_provider(self.provider, provider_cfg)
        self._update_mode_label()
        work_dir = os.getcwd()
        self.query_one("#title-bar", Static).update(self._make_banner(provider_cfg.model, work_dir))
        self.query_one("#model-label", Static).update(provider_cfg.model)

        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        if chat_input is not None:
            chat_input.disabled = False
            chat_input.focus()
        self.state = SessionState.IDLE

    def _initialize_provider(self, provider_cfg: ProviderConfig, provider: LLMProvider) -> bool:
        try:
            self.session.bind_model(provider.model)
            if self.extractor is not None:
                self.extractor.bind_provider(provider)
                self._extractor_task = asyncio.create_task(self.extractor.run())
            if self.governor is not None:
                self.governor.bind_provider(provider)
            self.agent = self._assemble_provider_agent(provider_cfg, provider)
            if self.hook_engine is not None:
                self.hook_engine.bind_subagent_runtime(
                    self.agent,
                    self.subagent_catalog,
                    self._notify_subagent_hook,
                )
            self.session.bind_agent(self.agent, self._current_tool_defs)
            self._configure_agent_tools(self.agent)
            agent_tool = self._tool_registry.get("Agent")
            if agent_tool is not None and hasattr(agent_tool, "set_parent"):
                agent_tool.set_parent(self.agent)
            self._load_skill_tool.set_agent(self.agent)
            self.skill_executor = SkillExecutor(
                self.agent,
                conversation=lambda: self.conv,
                provider_factory=lambda model: new_provider(replace(provider_cfg, model=model)),
            )
            self._sync_skills()
        except Exception as exc:
            logger.warning(
                "provider resource binding failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            self.agent = None
            if self.extractor is not None and self._extractor_task is not None:
                asyncio.create_task(self.extractor.close())
            return False
        self.provider_cfg = provider_cfg
        self.provider = provider
        if self.governor is not None and hasattr(self.governor, "maybe_schedule"):
            try:
                self.governor.maybe_schedule(datetime.now(UTC))
            except Exception as exc:
                logger.warning("memory governor scheduling failed: %s", type(exc).__name__)
        return True

    def _configure_agent_tools(self, agent: Agent) -> None:
        if self.coordinator_mode:
            from novacode.coordinator import allowed_tools, system_prompt_suffix

            agent.set_allowed_tools(allowed_tools())
            agent.append_system_prompt(system_prompt_suffix())
        elif self._agent_factory is None:
            hidden = {"TaskCreate", "TaskUpdate"}
            agent.set_allowed_tools(
                [item.name for item in self._tool_registry.definitions() if item.name not in hidden]
            )

    def _assemble_provider_agent(self, config: ProviderConfig, provider: LLMProvider) -> Agent:
        if self._agent_factory is not None:
            return self._agent_factory(provider, self._tool_registry, self.session)
        return assemble_agent(
            provider,
            self._tool_registry,
            config,
            self.session,
            factory=Agent,
            bind_model=False,
            version=self._version,
            engine=self.engine,
            instructions=self.instructions,
            memory_index=self._memory_index,
            hook_engine=self.hook_engine,
            context_compression=self.context_compression,
            progressive_tool_schema=self.progressive_tool_schema,
        )

    # ── right-click copy ───────────────────────────────────────

    def on_click(self, event: events.Click) -> None:
        """右键 → 智能复制：输入框内复制选中文本，否则复制最后 AI 回复。"""
        if event.button != 3:
            return
        try:
            inp = self.query_one("#chat-input", ChatInput)
            if inp.selected_text:
                self.copy_to_clipboard(inp.selected_text)
                self._flash_copy_feedback()
                return
        except Exception:
            pass
        if self._last_ai_text:
            try:
                self.copy_to_clipboard(self._last_ai_text)
                self._flash_copy_feedback()
            except Exception:
                pass

    def _flash_copy_feedback(self) -> None:
        try:
            label = self.query_one("#mode-label", Static)
            label.update(Text("  Copied!", style="bold #875FFF"))
            self.set_timer(1.5, self._update_mode_label)
        except Exception:
            pass

    # ── mode label ─────────────────────────────────────────────

    def _update_mode_label(self) -> None:
        try:
            label = self.query_one("#mode-label", Static)
        except Exception:
            return
        suffix = " [COORDINATOR]" if self.coordinator_mode else ""
        label.update(Text(f"  {self._mode.label()}{suffix}", style=self._mode_style()))

    def _mode_style(self) -> str:
        styles = {
            Mode.DEFAULT: "dim",
            Mode.ACCEPT_EDITS: "bold #58a6ff",
            Mode.PLAN: "bold #ffa500",
            Mode.BYPASS: "bold #f85149",
        }
        return styles.get(self._mode, "dim")

    # ── provider selector ──────────────────────────────────────

    @on(OptionList.OptionSelected)
    async def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "resume-list":
            session_id = str(event.option.id or "")
            info = self._resume_sessions.get(session_id)
            if info is not None:
                await self.resume_session(info)
            self._close_resume_list()
            return
        if event.option_list.id != "provider-list":
            return
        index = event.option_index
        assert index is not None
        self._select_provider(self.providers[index])
        await self.dispatch_session_start()

    # ── keys ────────────────────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        """全局按键分派——处理 Shift+Tab 和 approving 态按键。"""
        key = event.key

        if key == "escape":
            self.action_cancel()
            event.stop()
            event.prevent_default()
            return

        if self.state == SessionState.IDLE and self._handle_completion_key(event):
            return

        # Shift+Tab 循环切换权限模式（仅 idle 态）
        if key == "shift+tab" and self.state == SessionState.IDLE:
            event.stop()
            event.prevent_default()
            self._mode = _next_mode(self._mode)
            self._update_mode_label()
            self._show_system(f"已切换到 {self._mode.label()} 模式")
            return

        # Approving 态按键分派
        if self.state == SessionState.APPROVING:
            handled = self._update_approving(key)
            if handled:
                event.stop()
                event.prevent_default()
                return

    @on(TextArea.Changed, "#chat-input")
    def _on_chat_input_changed(self, event: TextArea.Changed) -> None:
        self._sync_completion_from_input(event.text_area.text)

    async def action_handle_ctrl_c(self) -> None:
        if self._exit_task is not None or self._shutdown_started:
            if self._extractor_task is not None and not self._extractor_task.done():
                self._show_system("正在取消记忆提取及剩余队列…")
                self._extractor_task.cancel()
            return
        # 输入框中有选中文本 → 优先复制，不触发取消/退出
        try:
            inp = self.query_one("#chat-input", ChatInput)
            if inp.selected_text:
                self.copy_to_clipboard(inp.selected_text)
                self._flash_copy_feedback()
                return
        except Exception:
            pass

        # STREAMING 或 APPROVING 态 → 取消本轮
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            self._signal_turn_cancel()
            return
        self.quit()

    def action_cancel(self) -> None:
        if self.completion.active:
            self.completion.hide()
            self._render_completion()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self.state == SessionState.RESUMING:
            self._close_resume_list()
            self.state = SessionState.IDLE
            return
        if self.state in (SessionState.STREAMING, SessionState.APPROVING):
            self._signal_turn_cancel()

    def _signal_turn_cancel(self) -> None:
        if self.turn_cancel is not None and self.turn_cancel.is_set():
            if self._agent_task is not None and not self._agent_task.done():
                self._agent_task.cancel()
            self._finish_cancelled()
            return
        if self.turn_cancel is not None:
            self.turn_cancel.set()
        if self.pending is not None:
            self._commit_approval(Outcome.DENY_ONCE)

    def action_toggle_tool_blocks(self) -> None:
        """Ctrl+O 切换所有工具块展开/折叠（预留）。"""
        pass

    # ── approving 交互 ────────────────────────────────────────

    def _update_approving(self, key: str) -> bool:
        """处理 approving 态按键。返回 True 表示已处理。"""
        if key in ("up", "k"):
            self.approve_cursor = (self.approve_cursor - 1) % 3
            self._refresh_approval_view()
            return True
        if key in ("down", "j"):
            self.approve_cursor = (self.approve_cursor + 1) % 3
            self._refresh_approval_view()
            return True
        if key in ("enter", "space"):
            self._commit_approval(_outcome_for_index(self.approve_cursor))
            return True
        if key == "1":
            self._commit_approval(Outcome.ALLOW_ONCE)
            return True
        if key == "2":
            self._commit_approval(Outcome.ALLOW_FOREVER)
            return True
        if key == "3":
            self._commit_approval(Outcome.DENY_ONCE)
            return True
        if key == "y":
            self._commit_approval(Outcome.ALLOW_ONCE)
            return True
        if key in ("n", "d"):
            self._commit_approval(Outcome.DENY_ONCE)
            return True
        return False

    def _commit_approval(self, outcome: Outcome) -> None:
        """提交人在回路决策并恢复 STREAMING。"""
        if self.pending is None:
            return
        pending = self.pending
        self.pending = None
        self.approve_cursor = 0
        self._approval_widget = None
        self.state = SessionState.STREAMING
        if not pending.respond.done():
            pending.respond.set_result(outcome)

    def _refresh_approval_view(self) -> None:
        """刷新待批准块——更新已有 widget 而非重复 mount。"""
        if self.pending is None or self._current_ai_row is None:
            return
        block = approval_block(self.pending, self.approve_cursor)
        if self._approval_widget is not None:
            self._approval_widget.update(block)
        else:
            # 兜底：widget 被意外清空时重新 mount
            self._approval_widget = Static(block, classes="approval-block")
            asyncio.ensure_future(self._current_ai_row.mount(self._approval_widget))
        self._scroll_chat()

    # ── submit ─────────────────────────────────────────────────

    @on(ChatInput.Submitted)
    async def _on_submit(self, event: ChatInput.Submitted) -> None:
        if self.provider is None:
            return
        text = event.text.strip() if event.text else ""
        if not text:
            return

        if await self.dispatch_slash(text):
            self.completion.hide()
            self._render_completion()
            return

        if self.state != SessionState.IDLE:
            return
        outcome = await self._dispatch_hook(
            HookEvent.USER_PROMPT_SUBMIT,
            prompt=text,
        )
        if outcome.blocked:
            self.query_one("#chat-input", ChatInput).text = event.text
            self.println(f"[hook {outcome.blocking_hook_name}] {outcome.reason}")
            return
        await self._dispatch(text)

    async def dispatch_slash(self, text: str) -> bool:
        """分发斜杠命令；非命令输入返回 False。"""
        name, is_slash = parse(text)
        if not is_slash:
            return False
        command = self.cmd_registry.lookup(name)
        if command is None:
            self.println("未知命令：输入 /help 查看可用命令")
            return True
        if command.kind in (Kind.UI, Kind.PROMPT) and not self.idle():
            self.error("请等待当前任务完成")
            return True
        try:
            self._command_args = arguments(text)
            await command.handler(self)
        except Exception as exc:
            self.error(str(exc))
        finally:
            self._command_args = ""
        return True

    def println(self, message: str) -> None:
        self._show_system(message)

    def error(self, message: str) -> None:
        self._show_system(f"✖ {message}")

    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        self._mode = mode
        self._update_mode_label()

    async def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        await self._dispatch(preset_prompt, display_label)

    def command_args(self) -> str:
        return self._command_args

    async def append_assistant_message(self, message: str, request: str = "") -> None:
        if not message:
            return
        if request:
            await self.session.record(Message(role="user", content=request))
        await self.session.record(Message(role="assistant", content=message))
        self._last_ai_text = message
        chat = self.query_one("#chat-area", VerticalScroll)
        row = Vertical(classes="ai-row")
        await chat.mount(row)
        await row.mount(Markdown(message, classes="message ai-message"))
        self._scroll_chat()

    def usage_in(self) -> int:
        return self._usage_in

    def usage_out(self) -> int:
        return self._usage_out

    def model_name(self) -> str:
        return self.provider.model if self.provider is not None else ""

    def cwd(self) -> str:
        return self._effective_cwd()

    def _effective_cwd(self) -> str:
        return self.active_cwd or str(self.project_root)

    def _set_active_cwd(self, value: str) -> None:
        self.active_cwd = value

    def worktree_accessor(self):
        if self.worktree_mgr is None:
            return None
        if self._worktree_adapter is None:
            from novacode.tui.worktree_adapter import WorktreeAdapter

            self._worktree_adapter = WorktreeAdapter(self.worktree_mgr, self._set_active_cwd)
        return self._worktree_adapter

    def tool_count(self) -> int:
        return self._tool_registry.count()

    def memory_files(self) -> list[str]:
        if self.extractor is not None:
            stores = (self.extractor.project_store, self.extractor.user_store)
        elif self.governor is not None:
            stores = self.governor.stores
        else:
            return []
        return [name for store in stores for name in store.list_files()]

    def session_path(self) -> str:
        try:
            return str(self.session.path)
        except Exception:
            return ""

    def session_id(self) -> str:
        return self.session.session_id

    @property
    def conv(self) -> Conversation:
        return self.session.conversation

    @property
    def session_context(self) -> SessionContext | None:
        return self.session.context

    def hook_sources(self) -> list[str]:
        return self.hook_engine.sources if self.hook_engine is not None else []

    def hook_rules(self):
        return self.hook_engine.rules if self.hook_engine is not None else []

    def _hook_payload(self, **values) -> dict:
        return {
            "session_id": self.session_id(),
            "cwd": str(self.project_root),
            "mode": str(self._mode),
            **values,
        }

    async def _dispatch_hook(self, event: HookEvent, **values):
        from novacode.hook import DispatchResult

        if self.hook_engine is None:
            return DispatchResult()
        result = await self.hook_engine.dispatch(event, self._hook_payload(**values))
        if self.agent is not None:
            self.agent.runtime.append_reminders(result.injected_prompts)
        return result

    async def dispatch_session_start(self) -> None:
        await self.session.start()

    async def end_session(self) -> None:
        await self.session.end()

    async def _dispatch_session_hook(self, event: HookEvent) -> None:
        await self._dispatch_hook(event)

    def quit(self) -> None:
        if self._exit_task is None:
            # 返回消息循环，等待期间仍能处理第二次 Ctrl+C。
            self._exit_task = asyncio.create_task(self._shutdown_and_exit())

    async def _shutdown_and_exit(self) -> None:
        await self._shutdown_resources()
        self.exit()

    async def force_compact(self) -> None:
        from novacode.agent.context_manager import CompressionDisabledError

        if self.agent is None:
            self.error("压缩失败：当前没有可用 Agent")
            return
        try:
            before, after = await self.agent.run_force_compact(
                self.session.conversation, self._current_tool_defs(), mode=self._mode
            )
            await self.session.sync()
        except CompressionDisabledError as exc:
            self.println(str(exc))
            return
        except Exception as exc:
            self.println(format_compact_notice(CompactPhase.AFTER_AUTO, 0, 0, exc))
            return
        self.println(format_compact_notice(CompactPhase.AFTER_AUTO, before, after, None))

    async def open_resume_menu(self) -> None:
        await self._begin_resume()

    async def clear_and_new_session(self) -> None:
        if self.agent is None or self.provider is None:
            self.error("清空失败：当前会话资源不完整")
            return
        try:
            await self.session.new_session()
            self.iter = 0
            self._usage_in = 0
            self._usage_out = 0
            self._last_ai_text = ""
            await self.query_one("#chat-area", VerticalScroll).remove_children()
            self.println("已清空当前会话，开启新 session")
        except Exception as exc:
            self.error(f"清空失败：{exc}")

    def idle(self) -> bool:
        return self.state == SessionState.IDLE

    def _sync_skills(self) -> None:
        catalog = self.skill_loader.get_catalog()
        text = ""
        if catalog:
            items = "\n".join(f"- {name}: {description}" for name, description in catalog)
            text = (
                "## Available Skills\n\n"
                f"{items}\n\n"
                "If the user's request matches a Skill, call LoadSkill with its name."
            )
        if self.agent is not None:
            self.agent.set_skill_catalog(text)
        if self.skill_executor is not None:
            register_skill_commands(
                self.cmd_registry,
                self.skill_loader,
                self.skill_executor,
            )

    def _handle_completion_key(self, event: events.Key) -> bool:
        if not self.completion.active:
            return False
        if event.key == "enter":
            text = self.query_one("#chat-input", ChatInput).text.strip()
            if any(char.isspace() for char in text):
                self.completion.hide()
                self._render_completion()
                return False
        if event.key == "up":
            self.completion.move_up()
        elif event.key == "down":
            self.completion.move_down()
        elif event.key == "escape":
            self.completion.hide()
        elif event.key in ("enter", "tab"):
            selected = self.completion.selected()
            self.completion.hide()
            self._render_completion()
            if selected is None and event.key == "enter":
                return False
            if selected is not None:
                self.query_one("#chat-input", ChatInput).clear()
                asyncio.create_task(self.dispatch_slash(f"/{selected.name}"))
        else:
            return False
        self._render_completion()
        event.stop()
        event.prevent_default()
        return True

    def _sync_completion_from_input(self, text: str) -> None:
        self.completion.update(text, self.cmd_registry)
        self._render_completion()

    def _render_completion(self) -> None:
        try:
            widget = self.query_one("#completion-menu", Static)
        except Exception:
            return
        widget.display = self.completion.active
        widget.update(self.completion.render(self.size.width))

    def _current_tool_defs(self):
        if self.agent is not None:
            return self.agent._tool_definitions(self.agent.permission_mode or self._mode)
        if self._mode == Mode.PLAN:
            return self._tool_registry.read_only_definitions()
        return self._tool_registry.definitions()

    async def _begin_resume(self) -> None:
        if self.session_context is None:
            self._show_system("恢复失败：当前会话未启用持久化")
            return
        sessions = list_sessions(Path(self.session_context.message_path).parent)
        if not sessions:
            self._show_system("没有可恢复的历史会话")
            return
        self._resume_sessions = {info.session_id: info for info in sessions}
        self._resume_list = OptionList(*build_resume_options(sessions), id="resume-list")
        self.state = SessionState.RESUMING
        chat = self.query_one("#chat-area", VerticalScroll)
        await chat.mount(self._resume_list)
        self._resume_list.focus()

    def _close_resume_list(self) -> None:
        if self._resume_list is not None:
            try:
                self._resume_list.remove()
            except Exception:
                pass
        self._resume_list = None
        self._resume_sessions = {}
        if self.state == SessionState.RESUMING:
            self.state = SessionState.IDLE

    async def resume_session(self, info: SessionInfo) -> bool:
        if self.session_context is None or self.agent is None:
            self._show_system("恢复失败：当前会话资源不完整")
            return False
        self.state = SessionState.RESUMING
        try:
            await self.session.resume(info)
            self._show_system(f"已恢复会话 {info.session_id}")
            return True
        except Exception as exc:
            self._show_system(f"恢复失败：{exc}")
            return False
        finally:
            if self.state == SessionState.RESUMING:
                self.state = SessionState.IDLE

    async def _dispatch(self, text: str, display_text: str | None = None) -> None:
        try:
            await self.session.record(Message(role="user", content=text))
        except Exception as exc:
            self._show_system(f"Session persistence failed: {exc}")
            return
        chat = self.query_one("#chat-area", VerticalScroll)
        # 用户消息
        user_row = Vertical(classes="user-row")
        await chat.mount(user_row)
        rich_text = Text()
        rich_text.append("❯ ", style="bold #58a6ff")
        rich_text.append(display_text or text, style="bold #c9d1d9")
        user_bubble = Static(rich_text, classes="message user-message")
        await user_row.mount(user_bubble)
        self._scroll_chat()
        await self._start_stream()

    async def _start_stream(self) -> None:
        self.state = SessionState.STREAMING
        self.turn_start = time.monotonic()
        self.iter = 0
        self.cur_tools = []
        self.turn_cancel = asyncio.Event()
        self._text_chunks.clear()
        self._stream_dirty = False
        self._current_ai_row = None
        self._streaming_label = None
        self._approval_widget = None
        self.pending = None
        self.approve_cursor = 0

        chat = self.query_one("#chat-area", VerticalScroll)
        self._spinner_idx = 0
        self._spinner_label = Static(f"  {SPINNER_FRAMES[0]} Thinking…", id="spinner-live")
        await chat.mount(self._spinner_label)
        self._scroll_chat()
        self._start_spinner()

        if self.agent is None:
            assert self.provider is not None
            self.agent = Agent(
                self.provider,
                self._tool_registry,
                self._version,
                self.engine,
                context_compression=self.context_compression,
                progressive_tool_schema=self.progressive_tool_schema,
            )
        agent = self.agent
        with with_cwd(self._effective_cwd()):
            self._agent_task = asyncio.create_task(
                self._consume_events(agent.run(self.conv, self._mode, self.turn_cancel))
            )

    # ── spinner ────────────────────────────────────────────────

    def _start_spinner(self) -> None:
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._spinner_label is not None:
            self._spinner_label.remove()
            self._spinner_label = None

    def _tick_spinner(self) -> None:
        self._spinner_idx += 1
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        elapsed = time.monotonic() - self.turn_start
        if self._spinner_label is not None:
            self._spinner_label.update(f"  {frame} Thinking…  ({elapsed:.0f}s)")
            if self._spinner_idx % 5 == 0:
                self._scroll_chat()

    # ── consume agent events ───────────────────────────────────

    async def _consume_events(self, agent_gen) -> None:
        turn_cancel = self.turn_cancel
        try:
            async for ev in agent_gen:
                if ev.err is not None:
                    self._finish_with_error(ev.err)
                    return

                if ev.compact is not None:
                    self._show_system(
                        format_compact_notice(
                            ev.compact.phase,
                            ev.compact.before,
                            ev.compact.after,
                            ev.compact.err,
                            accepted=ev.compact.accepted,
                        )
                    )
                    continue

                if ev.approval is not None:
                    # 所有来源共用一个 FIFO，由唯一消费者逐个显示。
                    self._flush_preamble()
                    await self.task_mgr.subscribe_approvals().put(ev.approval)
                    continue

                if ev.tool is not None and ev.tool.phase == Phase.START:
                    self._flush_preamble()
                    self.cur_tools.append(ToolDisplay(name=ev.tool.name, args=ev.tool.args))
                elif ev.tool is not None and ev.tool.phase == Phase.END:
                    td = (
                        self.cur_tools.pop(0)
                        if self.cur_tools
                        else ToolDisplay(name=ev.tool.name, args=ev.tool.args)
                    )
                    self._mount_tool_block(ev.tool.name, td.args, ev.tool.result, ev.tool.is_error)

                if ev.usage is not None:
                    self._usage_in += ev.usage.input
                    self._usage_out += ev.usage.output

                if ev.notice:
                    self._show_system(ev.notice)

                if ev.iter > 0:
                    self.iter = ev.iter

                if ev.text:
                    self._text_chunks.append(ev.text)
                    self._stream_dirty = True
                    if self._stream_timer is None:
                        self._stream_timer = self.set_interval(0.03, self._flush_streaming_text)

                if ev.done:
                    await self.session.sync()
                    self._finish_with_assistant("".join(self._text_chunks))
                    if ev.memory_turn is not None and self.extractor is not None:
                        try:
                            self.extractor.submit(ev.memory_turn)
                        except Exception as exc:
                            self._show_system(f"Memory extraction enqueue failed: {exc}")
                    return

            if self.turn_cancel is not None and self.turn_cancel.is_set():
                self._finish_cancelled()

        except asyncio.CancelledError:
            if self.turn_cancel is turn_cancel and self.state in (
                SessionState.STREAMING,
                SessionState.APPROVING,
            ):
                self._finish_cancelled()
            raise
        except Exception as e:
            self._finish_with_error(e)

    async def _consume_task_done(self) -> None:
        queue = self.task_mgr.subscribe_done()
        while True:
            task_id = await queue.get()
            background = self.task_mgr.get(task_id)
            if background is None or self.agent is None:
                continue
            self.agent.runtime.append_reminders([build_task_notification(background)])

    async def _consume_lead_mail(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.team_mgr is None or self.agent is None:
                continue
            messages = await self.team_mgr.poll_lead_mailboxes()
            if messages:
                self.agent.runtime.append_reminders([build_team_update_reminder(messages)])
                self.lead_mail_event.set()
            if self.lead_mail_event.is_set() and self.state == SessionState.IDLE:
                self.lead_mail_event.clear()
                await self._dispatch("[team-update] 队员发来新消息，请按 Coordinator 流程处理。")

    async def _consume_task_approvals(self) -> None:
        queue = self.task_mgr.subscribe_approvals()
        while True:
            request = await queue.get()
            self.pending = request
            self.approve_cursor = 0
            self.state = SessionState.APPROVING
            self._mount_approval_block()
            await asyncio.shield(request.respond)
            if self._agent_task is None:
                self.state = SessionState.IDLE

    def _flush_preamble(self) -> None:
        if not self._text_chunks:
            return
        self._flush_streaming_text()
        text = "".join(self._text_chunks)
        self._text_chunks.clear()
        self._ensure_ai_row()
        if self._streaming_label is not None:
            self._streaming_label.remove()
            self._streaming_label = None
        md = Markdown(text, classes="message ai-message")
        if self._current_ai_row is not None:
            asyncio.ensure_future(self._current_ai_row.mount(md))

    def _ensure_ai_row(self) -> None:
        if self._current_ai_row is None:
            chat = self.query_one("#chat-area", VerticalScroll)
            self._current_ai_row = Vertical(classes="ai-row")
            asyncio.ensure_future(chat.mount(self._current_ai_row))

    def _flush_streaming_text(self) -> None:
        """只在固定周期或终止边界合并文本，没有新 chunk 时不重绘。"""
        if self._stream_dirty:
            self._update_streaming_label()
            self._stream_dirty = False

    def _update_streaming_label(self) -> None:
        self._ensure_ai_row()
        if self._streaming_label is None and self._current_ai_row is not None:
            self._streaming_label = Static("", classes="message ai-message")
            asyncio.ensure_future(self._current_ai_row.mount(self._streaming_label))
        if self._streaming_label is not None:
            t = Text()
            t.append("● ", style="bold #875FFF")
            t.append("".join(self._text_chunks))
            self._streaming_label.update(t)
        self._scroll_chat()

    def _mount_tool_block(self, name: str, args: str, result: str, is_error: bool) -> None:
        self._ensure_ai_row()
        line = tool_line(name, args)
        summary = tool_result_summary(result, is_error)
        if self._current_ai_row is not None:
            asyncio.ensure_future(self._current_ai_row.mount(Static(line, classes="tool-block")))
            asyncio.ensure_future(
                self._current_ai_row.mount(Static(summary, classes="tool-detail"))
            )
        self._scroll_chat()

    def _mount_approval_block(self) -> None:
        """挂载人在回路待批准块——首次创建并保存 widget 引用。"""
        self._ensure_ai_row()
        if self.pending is None:
            return
        block = approval_block(self.pending, self.approve_cursor)
        self._approval_widget = Static(block, classes="approval-block")
        if self._current_ai_row is not None:
            asyncio.ensure_future(self._current_ai_row.mount(self._approval_widget))
        self._scroll_chat()

    def _finish_with_assistant(self, reply: str) -> None:
        self._flush_streaming_text()
        self._stop_spinner()
        elapsed = time.monotonic() - self.turn_start
        self._last_ai_text = reply.strip()

        if reply.strip():
            self._ensure_ai_row()
            if self._streaming_label is not None:
                self._streaming_label.remove()
                self._streaming_label = None
            md = Markdown(reply, classes="message ai-message")
            if self._current_ai_row is not None:
                asyncio.ensure_future(self._current_ai_row.mount(md))

        verb = "Thinking"
        done_text = Text(f"✻ {verb}d for {elapsed:.1f}s", style="dim italic")
        done_label = Static(done_text, classes="message thinking-done")
        if self._current_ai_row is not None:
            asyncio.ensure_future(self._current_ai_row.mount(done_label))

        self._scroll_chat()
        self._finish_streaming()

    def _finish_with_error(self, err: Exception) -> None:
        self._flush_streaming_text()
        self._stop_spinner()
        name = type(err).__name__
        self._show_system(f"✖ {name}: {err}")
        self._finish_streaming()

    def _finish_cancelled(self) -> None:
        self._flush_streaming_text()
        self._show_system("(response interrupted)")
        self._finish_streaming()

    def _finish_streaming(self) -> None:
        self._flush_streaming_text()
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        self._stop_spinner()
        self._agent_task = None
        self.state = SessionState.IDLE
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
        self._current_ai_row = None
        self._streaming_label = None
        self._text_chunks.clear()
        self._stream_dirty = False
        self._approval_widget = None
        self.pending = None
        self.approve_cursor = 0
        try:
            inp = self.query_one("#chat-input", ChatInput)
            inp.focus()
        except Exception:
            pass

    # ── helpers ────────────────────────────────────────────────

    def _scroll_chat(self) -> None:
        try:
            chat = self.query_one("#chat-area", VerticalScroll)
            self.call_after_refresh(chat.scroll_end, animate=False)
        except Exception:
            pass

    def _show_system(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        widget = Static(Text(f"  {text}", style="dim"), classes="message system-message")
        chat.mount(widget)
        self._scroll_chat()

    async def _shutdown_resources(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        try:
            self.query_one("#chat-input", ChatInput).disabled = True
            self._show_system("正在等待记忆提取完成；再次按 Ctrl+C 可取消提取及剩余队列。")
        except Exception:
            pass
        memory_close = asyncio.create_task(self.extractor.close()) if self.extractor else None
        try:
            await self.end_session()
        except Exception as exc:
            logger.warning("session end hook failed: %s", type(exc).__name__)
        if self.hook_engine is not None:
            try:
                await self.hook_engine.close()
            except Exception as exc:
                logger.warning("hook engine close failed: %s", type(exc).__name__)
        for task in self._task_consumers:
            task.cancel()
        if self._task_consumers:
            await asyncio.gather(*self._task_consumers, return_exceptions=True)
        self._task_consumers.clear()
        await self.task_mgr.close()

        if memory_close is not None:
            try:
                await memory_close
            except Exception as exc:
                logger.warning("memory extractor close failed: %s", type(exc).__name__)
        if self._extractor_task is not None:
            try:
                await self._extractor_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("memory extractor worker failed: %s", type(exc).__name__)
            self._extractor_task = None

        if self.governor is not None:
            try:
                await self.governor.close()
            except Exception as exc:
                logger.warning("memory governor close failed: %s", type(exc).__name__)

        if self.cleanup_task is not None:
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("session cleanup failed: %s", type(exc).__name__)
            self.cleanup_task = None

        try:
            await self.session.close()
        except Exception as exc:
            logger.warning("session service close failed: %s", type(exc).__name__)

        if self.provider is not None:
            try:
                await self.provider.close()
            except Exception as exc:
                logger.warning("provider close failed: %s", type(exc).__name__)
