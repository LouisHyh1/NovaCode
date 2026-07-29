"""Textual TUI application — NovaCodeApp."""

import asyncio
import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Markdown, OptionList, Static, TextArea

from novacode import __version__
from novacode.agent import Agent, ApprovalRequest, CompactPhase, Phase, SessionRuntime
from novacode.command import Kind, arguments, parse, register_builtins
from novacode.command import Registry as CommandRegistry
from novacode.command.builtin_skill import register_skill_management
from novacode.command.skill_register import register_skill_commands
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    new_session_context,
    open_session_context,
)
from novacode.compact.const import auto_compact_threshold
from novacode.compact.token import estimate_tokens
from novacode.config import ProviderConfig, effective_context_window
from novacode.conversation import Conversation
from novacode.hook import Engine as HookEngine
from novacode.hook import Event as HookEvent
from novacode.llm import Provider as LLMProvider
from novacode.llm import new_provider
from novacode.memory import MemoryExtractor, MemoryGovernor
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.prompt import system_reminder
from novacode.session import SessionInfo, SessionWriter, list_sessions, load_session
from novacode.skills import SkillExecutor, SkillLoader
from novacode.tool import Registry as ToolRegistry
from novacode.tool.install_skill import InstallSkillTool
from novacode.tool.load_skill import LoadSkill
from novacode.tui.commands import format_compact_notice
from novacode.tui.complete import CompletionMenu
from novacode.tui.resume import build_resume_options
from novacode.tui.view import approval_block, tool_line, tool_result_summary

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

    async def _on_key(self, event: "events.Key") -> None:
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
        await super()._on_key(event)


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
        session_context: SessionContext | None = None,
        writer: SessionWriter | None = None,
        extractor: MemoryExtractor | None = None,
        governor: MemoryGovernor | None = None,
        instructions: str = "",
        memory_index: str | Callable[[], str] = "",
    ) -> None:
        super().__init__(driver_class=driver_class)
        self._version = version or __version__
        self.providers = providers
        self.provider: LLMProvider | None = None
        self.provider_cfg: ProviderConfig | None = None
        self.agent: Agent | None = None
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.session_context = session_context
        self.writer = writer
        self.extractor = extractor
        self.governor = governor
        self.instructions = instructions
        self._memory_index = memory_index if callable(memory_index) else lambda: memory_index
        self._extractor_task: asyncio.Task[None] | None = None
        self.cleanup_task: asyncio.Task | None = None
        self._shutdown_started = False
        self._pending_background_notices: list[str] = []
        self.conv = Conversation(
            writer.append_message if writer is not None else None,
            writer.append_compaction if writer is not None else None,
        )
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
        self._session_started = False
        self._session_ended = False
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
        self._accumulated_text: str = ""
        self._spinner_label: Static | None = None
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._last_ai_text: str = ""
        self._session_switch_lock = asyncio.Lock()
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

    async def on_unmount(self) -> None:
        await self._shutdown_resources()

    def notify_background(self, notice: str) -> None:
        try:
            self._show_system(notice)
        except Exception:
            self._pending_background_notices.append(notice)

    def _select_provider(self, provider_cfg: ProviderConfig) -> None:
        if self.provider is not None and self.agent is not None:
            return
        try:
            chat_input = self.query_one("#chat-input", ChatInput)
            chat_input.disabled = True
        except Exception:
            chat_input = None
        try:
            provider = new_provider(provider_cfg)
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
        runtime = self._new_runtime(self.session_context) if self.session_context else None
        try:
            if self.writer is not None:
                self.writer.bind_model(provider.model)
            if self.extractor is not None:
                self.extractor.bind_provider(provider)
                self._extractor_task = asyncio.create_task(self.extractor.run())
            self.agent = Agent(
                provider,
                self._tool_registry,
                self._version,
                self.engine,
                runtime=runtime,
                context_window=effective_context_window(provider_cfg),
                instructions=self.instructions,
                memory_index=self._memory_index,
                hook_engine=self.hook_engine,
            )
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
        return True

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
        label.update(Text(f"  {self._mode.label()}", style=self._mode_style()))

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

    async def _on_key(self, event: "events.Key") -> None:
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

        # 默认处理链
        await super()._on_key(event)

    @on(TextArea.Changed, "#chat-input")
    def _on_chat_input_changed(self, event: TextArea.Changed) -> None:
        self._sync_completion_from_input(event.text_area.text)

    async def action_handle_ctrl_c(self) -> None:
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
        await self.end_session()
        self.exit()

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
        if self.turn_cancel is not None:
            self.turn_cancel.set()
        if self.pending is not None and not self.pending.respond.done():
            self.pending.respond.set_result(Outcome.DENY_ONCE)

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
            self.conv.add_user(request)
        self.conv.add_assistant(message)
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
        return str(self.project_root)

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
        return str(self.writer.path) if self.writer is not None else ""

    def session_id(self) -> str:
        return self.session_context.session_id if self.session_context is not None else ""

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
        if self.agent is None or (self._session_started and not self._session_ended):
            return
        await self._dispatch_hook(HookEvent.SESSION_START)
        self._session_started = True
        self._session_ended = False

    async def dispatch_session_resume(self) -> None:
        await self._dispatch_hook(HookEvent.SESSION_RESUME)
        self._session_started = True
        self._session_ended = False

    async def end_session(self) -> None:
        if not self._session_started or self._session_ended:
            return
        await self._dispatch_hook(HookEvent.SESSION_END)
        self._session_ended = True

    def quit(self) -> None:
        self.exit()

    async def force_compact(self) -> None:
        if self.agent is None:
            self.error("压缩失败：当前没有可用 Agent")
            return
        try:
            before, after = await self.agent.run_force_compact(
                self.conv, self._current_tool_defs(), mode=self._mode
            )
        except Exception as exc:
            self.println(format_compact_notice(CompactPhase.AFTER_AUTO, 0, 0, exc))
            return
        self.println(format_compact_notice(CompactPhase.AFTER_AUTO, before, after, None))

    async def open_resume_menu(self) -> None:
        await self._begin_resume()

    async def clear_and_new_session(self) -> None:
        if self.writer is None or self.agent is None or self.provider is None:
            self.error("清空失败：当前会话资源不完整")
            return
        old_writer = self.writer
        new_writer: SessionWriter | None = None
        try:
            context = new_session_context(str(self.project_root))
            new_writer = SessionWriter(
                Path(context.message_path).parent,
                context.session_id,
                self.provider.model,
            )
            conversation = Conversation(
                new_writer.append_message,
                new_writer.append_compaction,
            )
            async with self._session_switch_lock:
                await self.end_session()
                self.agent.runtime.reset_for_new_session(context)
                await self.agent.runtime.reset_hooks_for_new_session()
                self.agent.clear_active_skills()
                self.session_context = context
                self.writer = new_writer
                self.conv = conversation
                new_writer = None
            self.iter = 0
            self._usage_in = 0
            self._usage_out = 0
            self._last_ai_text = ""
            await self.query_one("#chat-area", VerticalScroll).remove_children()
            await asyncio.to_thread(old_writer.close)
            self.println("已清空当前会话，开启新 session")
            await self.dispatch_session_start()
        except Exception as exc:
            if new_writer is not None:
                await asyncio.to_thread(new_writer.close)
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
        if self._mode == Mode.PLAN:
            return self._tool_registry.read_only_definitions()
        return self._tool_registry.definitions()

    @staticmethod
    def _new_runtime(
        session_context: SessionContext,
        resume_reminder: str = "",
    ) -> SessionRuntime:
        return SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=session_context,
            resume_reminder=resume_reminder,
        )

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
        if self.writer is None or self.session_context is None or self.agent is None:
            self._show_system("恢复失败：当前会话资源不完整")
            return False

        old_writer = self.writer
        staging_root: Path | None = None
        promoted: list[Path] = []
        new_writer: SessionWriter | None = None
        committed = False
        switched = False
        self.state = SessionState.RESUMING
        try:
            loaded = load_session(info.path)
            if not loaded.messages or not loaded.model:
                raise ValueError("会话没有可恢复的有效消息")
            target_context = open_session_context(str(self.project_root), info.session_id)
            candidate = Conversation.from_messages(loaded.messages)
            reminder = self._build_resume_reminder(loaded.last_activity)
            target_runtime = self._new_runtime(target_context, reminder)
            compacted = self._needs_resume_compaction(candidate)

            if compacted:
                staging_root = info.path.parent / f".resume-staging-{uuid.uuid4().hex}"
                staging_spill = staging_root / "tool-results"
                staging_spill.mkdir(parents=True)
                staging_context = SessionContext(
                    session_id=staging_root.name,
                    message_path=str(staging_root / "messages.jsonl"),
                    spill_dir=str(staging_spill),
                )
                await self._compact_resume_candidate(
                    candidate,
                    self._new_runtime(staging_context),
                )
                promoted = self._promote_staged_spills(
                    candidate,
                    staging_spill,
                    Path(target_context.spill_dir),
                )

            new_writer = SessionWriter.open_existing(
                info.path.parent,
                info.session_id,
                loaded.model,
            )
            if compacted:
                new_writer.append_compaction(candidate.messages())
                committed = True
            live_conversation = Conversation.from_messages(
                candidate.messages(),
                new_writer.append_message,
                new_writer.append_compaction,
            )
            async with self._session_switch_lock:
                await self.end_session()
                if self.hook_engine is not None:
                    await self.hook_engine.reset_for_new_session()
                target_runtime.hook_engine = self.hook_engine
                self._swap_session(
                    live_conversation,
                    new_writer,
                    target_runtime,
                    target_context,
                )
            switched = True
            new_writer = None
            try:
                await asyncio.to_thread(old_writer.close)
            except Exception as exc:
                logger.warning("old session writer close failed: %s", exc)
            self._show_system(f"已恢复会话 {info.session_id}")
            await self.dispatch_session_resume()
            return True
        except Exception as exc:
            if new_writer is not None:
                try:
                    new_writer.close()
                except Exception:
                    pass
            if promoted and not committed:
                self._remove_promoted(promoted)
            if committed and not switched:
                self._show_system(f"恢复已提交但未切换：{exc}")
            else:
                self._show_system(f"恢复失败：{exc}")
            return False
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)
            if self.state == SessionState.RESUMING:
                self.state = SessionState.IDLE

    def _needs_resume_compaction(self, candidate: Conversation) -> bool:
        if self.agent is None:
            return False
        estimated = estimate_tokens(0, candidate.messages(), 0)
        return estimated > auto_compact_threshold(self.agent.context_window)

    async def _compact_resume_candidate(
        self,
        candidate: Conversation,
        staging_runtime: SessionRuntime,
    ) -> None:
        if self.agent is None:
            raise RuntimeError("no active agent")
        await self.agent.run_force_compact(
            candidate,
            self._current_tool_defs(),
            runtime=staging_runtime,
        )

    def _promote_staged_spills(
        self,
        candidate: Conversation,
        staging_spill_dir: Path,
        target_spill_dir: Path,
    ) -> list[Path]:
        target_spill_dir.mkdir(parents=True, exist_ok=True)
        promoted: list[Path] = []
        path_map: dict[str, str] = {}
        try:
            for source in sorted(staging_spill_dir.rglob("*")):
                if not source.is_file():
                    continue
                target = self._reserve_spill_path(target_spill_dir, source.name)
                try:
                    shutil.copyfile(source, target)
                    source.unlink()
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                promoted.append(target)
                path_map[str(source)] = str(target)

            messages = candidate.messages()
            for message in messages:
                for result in message.tool_results:
                    for source, target in path_map.items():
                        result.content = result.content.replace(source, target)
            candidate.replace_history(messages)
            return promoted
        except Exception:
            self._remove_promoted(promoted)
            raise

    @staticmethod
    def _reserve_spill_path(directory: Path, filename: str) -> Path:
        original = Path(filename)
        counter = 0
        while True:
            suffix = "" if counter == 0 else f"-{counter}"
            candidate = directory / f"{original.stem}{suffix}{original.suffix}"
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                counter += 1
                continue
            os.close(descriptor)
            return candidate

    @staticmethod
    def _remove_promoted(paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("promoted spill cleanup failed: %s", path)

    def _swap_session(
        self,
        conversation: Conversation,
        writer: SessionWriter,
        runtime: SessionRuntime,
        context: SessionContext,
    ) -> None:
        if self.agent is None:
            raise RuntimeError("no active agent")
        self.conv = conversation
        self.writer = writer
        self.session_context = context
        self.agent.runtime = runtime

    @staticmethod
    def _build_resume_reminder(
        last_activity: datetime | None,
        now: datetime | None = None,
    ) -> str:
        if last_activity is None:
            return ""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if current - last_activity.astimezone(UTC) <= timedelta(hours=24):
            return ""
        return system_reminder(
            "该恢复会话的历史信息可能已经过期。继续前请重新读取会变化的文件、配置和外部资料。"
        )

    async def _dispatch(self, text: str, display_text: str | None = None) -> None:
        try:
            self.conv.add_user(text)
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
        self._accumulated_text = ""
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
            self.agent = Agent(self.provider, self._tool_registry, self._version, self.engine)
        agent = self.agent
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
                        )
                    )
                    continue

                if ev.approval is not None:
                    # 人在回路——切到 APPROVING 态
                    if self._accumulated_text.strip():
                        self._flush_preamble()
                    self.pending = ev.approval
                    self.approve_cursor = 0
                    self.state = SessionState.APPROVING
                    self._mount_approval_block()
                    # 不要继续读事件——agent 正 await respond
                    continue

                if ev.tool is not None and ev.tool.phase == Phase.START:
                    if self._accumulated_text.strip():
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
                    self._accumulated_text += ev.text
                    self._update_streaming_label()

                if ev.done:
                    self._finish_with_assistant(self._accumulated_text)
                    if ev.memory_turn is not None and self.extractor is not None:
                        try:
                            self.extractor.submit(ev.memory_turn)
                        except Exception as exc:
                            self._show_system(f"Memory extraction enqueue failed: {exc}")
                    return

            if self.turn_cancel is not None and self.turn_cancel.is_set():
                self._show_system("(response interrupted)")
                self._finish_streaming()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._finish_with_error(e)

    def _flush_preamble(self) -> None:
        text = self._accumulated_text
        self._accumulated_text = ""
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
            if self._streaming_label is None:
                self._streaming_label = Static("", classes="message ai-message")
                asyncio.ensure_future(self._current_ai_row.mount(self._streaming_label))

    def _update_streaming_label(self) -> None:
        self._ensure_ai_row()
        if self._streaming_label is not None:
            t = Text()
            t.append("● ", style="bold #875FFF")
            t.append(self._accumulated_text)
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
        self._stop_spinner()
        name = type(err).__name__
        self._show_system(f"✖ {name}: {err}")
        self._finish_streaming()

    def _finish_streaming(self) -> None:
        self._stop_spinner()
        self._agent_task = None
        self.state = SessionState.IDLE
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
        self._current_ai_row = None
        self._streaming_label = None
        self._accumulated_text = ""
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
        except Exception:
            pass

        if self.extractor is not None:
            try:
                await self.extractor.close()
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

        if self.writer is not None and hasattr(self.writer, "close"):
            try:
                await asyncio.to_thread(self.writer.close)
            except Exception as exc:
                logger.warning("session writer close failed: %s", type(exc).__name__)
