"""用可控周期验收流式批次及所有终止路径，不依赖真实时间等待。"""

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from novacode.agent import Event, Phase, ToolEvent
from novacode.config import ProviderConfig
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp, SessionState


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.next_tick = 0.0
        self.interval = 0.0
        self.callback: Callable[[], None] = lambda: None
        self.timer = MagicMock()
        self.running = False
        self.timer.stop.side_effect = lambda: setattr(self, "running", False)

    def set_interval(self, interval: float, callback: Callable[[], None]) -> MagicMock:
        assert not self.running, "同一条流只创建一个周期定时器"
        self.interval = interval
        self.next_tick = self.now + interval
        self.callback = callback
        self.running = True
        return self.timer

    def advance(self, seconds: float) -> None:
        self.now += seconds
        while self.running and self.next_tick <= self.now:
            self.next_tick += self.interval
            self.callback()


def make_app() -> tuple[NovaCodeApp, Clock, list[tuple[str, str]]]:
    app = NovaCodeApp(
        [ProviderConfig("test", "openai", "test", "http://localhost", "test")],
        Registry(),
    )
    clock = Clock()
    observations: list[tuple[str, str]] = []
    label = MagicMock()
    label.update.side_effect = lambda text: observations.append(("text", text.plain[2:]))
    app.set_interval = clock.set_interval
    app._current_ai_row = MagicMock()
    app._current_ai_row.mount = AsyncMock()
    app._streaming_label = label
    app._ensure_ai_row = MagicMock()
    app._scroll_chat = MagicMock()
    app._stop_spinner = MagicMock()
    app._show_system = lambda text: observations.append(("status", text))
    app.session.sync = AsyncMock()
    app.state = SessionState.STREAMING
    app.turn_cancel = asyncio.Event()
    return app, clock, observations


@pytest.mark.asyncio
async def test_long_stream_batches_on_fixed_clock_and_preserves_every_chunk() -> None:
    app, clock, observed = make_app()
    chunks = [f"{index}:中文" * 20 for index in range(2000)]

    async def stream():
        for chunk in chunks[:1000]:
            yield Event(text=chunk)
        assert not observed
        clock.advance(0.029)
        assert not observed
        clock.advance(0.0011)
        assert observed == [("text", "".join(chunks[:1000]))]
        clock.advance(0.060)
        assert len(observed) == 1, "没有新文本时不重绘"
        for chunk in chunks[1000:]:
            yield Event(text=chunk)
        clock.advance(0.030)
        assert observed[-1] == ("text", "".join(chunks))
        yield Event(text="尾部", done=True)

    await app._consume_events(stream())
    assert clock.interval == pytest.approx(0.03)
    assert observed[-1] == ("text", "".join(chunks) + "尾部")
    assert app._last_ai_text == "".join(chunks) + "尾部"
    assert not clock.running
    app.session.sync.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["done", "cancel", "forced", "error", "exception"])
async def test_pending_text_flushes_before_termination(termination: str) -> None:
    app, clock, observed = make_app()

    async def stream():
        yield Event(text="尚未达到刷新周期的文本")
        assert not observed
        if termination == "done":
            yield Event(done=True)
        elif termination == "cancel":
            app.turn_cancel.set()
        elif termination == "forced":
            raise asyncio.CancelledError
        elif termination == "error":
            yield Event(err=RuntimeError("流失败"))
        else:
            raise RuntimeError("流失败")

    if termination == "forced":
        with pytest.raises(asyncio.CancelledError):
            await app._consume_events(stream())
    else:
        await app._consume_events(stream())
    assert observed[0] == ("text", "尚未达到刷新周期的文本")
    if termination == "done":
        assert app._last_ai_text == "尚未达到刷新周期的文本"
    elif termination in ("cancel", "forced"):
        assert observed[1] == ("status", "(response interrupted)")
    else:
        assert observed[1] == ("status", "✖ RuntimeError: 流失败")
    assert app.state == SessionState.IDLE
    assert not clock.running
    before = observed.copy()
    clock.advance(1)
    assert observed == before, "结束后的定时器不能继续渲染"


@pytest.mark.asyncio
async def test_tool_preamble_and_following_reply_use_separate_chunk_buffers(monkeypatch) -> None:
    app, clock, observed = make_app()
    app._mount_tool_block = MagicMock()
    row = app._current_ai_row
    label = app._streaming_label
    monkeypatch.setattr("novacode.tui.app.Static", MagicMock(return_value=label))

    async def stream():
        yield Event(text="工具前导")
        yield Event(tool=ToolEvent(phase=Phase.START, name="read_file", args="{}"))
        assert not app._text_chunks
        yield Event(text="工具后的最终回复")
        yield Event(done=True)

    await app._consume_events(stream())
    assert app._last_ai_text == "工具后的最终回复"
    assert observed == [("text", "工具前导"), ("text", "工具后的最终回复")]
    assert row.mount.call_args_list[0].args[0]._initial_markdown == "工具前导"
    assert row.mount.call_args_list[-2].args[0]._initial_markdown == "工具后的最终回复"
    assert app._current_ai_row is None
    assert not clock.running


@pytest.mark.asyncio
async def test_second_cancel_flushes_before_clearing_turn_state() -> None:
    app, clock, observed = make_app()

    async def stream():
        yield Event(text="强制取消前的尾部")
        await asyncio.Event().wait()

    task = asyncio.create_task(app._consume_events(stream()))
    app._agent_task = task
    await asyncio.sleep(0)
    app._signal_turn_cancel()
    app._signal_turn_cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert observed[0] == ("text", "强制取消前的尾部")
    assert observed[1] == ("status", "(response interrupted)")
    assert len(observed) == 2
    assert not clock.running


@pytest.mark.asyncio
async def test_cancelled_old_consumer_does_not_clear_a_new_turn() -> None:
    app, clock, observed = make_app()

    async def stream():
        yield Event(text="旧轮次")
        await asyncio.Event().wait()

    task = asyncio.create_task(app._consume_events(stream()))
    app._agent_task = task
    await asyncio.sleep(0)
    app._signal_turn_cancel()
    app._signal_turn_cancel()
    # 旧消费者处理 CancelledError 前，新一轮已提交。
    app.turn_cancel = asyncio.Event()
    app.state = SessionState.STREAMING
    app._text_chunks.append("新轮次")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state == SessionState.STREAMING
    assert app._text_chunks == ["新轮次"]
    assert len(observed) == 2


@pytest.mark.asyncio
async def test_mounted_tui_timer_renders_text_after_tool_preamble(monkeypatch) -> None:
    """真实 Textual 调度与控件验证，避免模拟 mount 掩盖第二段回复丢失。"""
    from textual.widgets import Markdown

    provider = MagicMock(model="test")
    provider.close = AsyncMock()
    monkeypatch.setattr("novacode.tui.app.new_provider", lambda cfg: provider)
    app = NovaCodeApp(
        [ProviderConfig("test", "openai", "test", "http://localhost", "test")], Registry()
    )
    async with app.run_test(size=(100, 30)) as pilot:
        app.state = SessionState.STREAMING
        app.turn_cancel = asyncio.Event()

        async def stream():
            yield Event(text="工具前导")
            yield Event(tool=ToolEvent(phase=Phase.START, name="read_file", args="{}"))
            yield Event(tool=ToolEvent(phase=Phase.END, name="read_file", result="工具结果"))
            yield Event(text="工具后的流式文本")
            await pilot.pause(0.08)
            assert app._streaming_label is not None
            assert "工具后的流式文本" in str(app._streaming_label.render())
            yield Event(text="和尾部", done=True)

        await app._consume_events(stream())
        await pilot.pause()
        assert app._last_ai_text == "工具后的流式文本和尾部"
        assert [widget.source for widget in app.query(Markdown)] == [
            "工具前导",
            "工具后的流式文本和尾部",
        ]
        assert app._stream_timer is None
    provider.close.assert_awaited_once()
