"""TUI App：状态机（选择/空闲/流式）、输入框、对话区、流式消费与计时、provider 选择。

说明：plan 中将 stream.py / select.py 拆为独立文件；本实现体量较小，按
plan 允许的"可合并"条款，将流式与选择逻辑并入 ``app.py`` 以保持单会话
交互的完整可读性，仅 ``view.py`` 独立（纯渲染函数，便于测试）。
"""

from __future__ import annotations

import asyncio
import os
import time
from enum import Enum

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.timer import Timer
from textual.widgets import OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

from koyocode import __version__
from koyocode.config import ProviderConfig
from koyocode.conversation import Conversation
from koyocode.llm import Provider, new_provider
from koyocode.prompt import render_banner

from .view import (
    assistant_block,
    error_block,
    render_statusbar,
    streaming_view,
    user_block,
)


class SessionState(Enum):
    """会话状态：选择 provider / 等待输入 / 接收流式。"""

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"


class InputArea(TextArea):
    """多行输入框：Enter 提交、Alt+Enter 插入换行。"""

    class Submitted(Message):
        """输入框提交（Enter）时发出，携带当前文本。"""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("alt+enter", "newline", "Newline", show=False, priority=True),
    ]

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")


class KoyoCodeApp(App):
    """KoyoCode 终端应用。"""

    CSS = """
    Screen { layout: vertical; }
    #log { height: 1fr; padding: 0 1; }
    #streaming { height: auto; max-height: 8; padding: 0 1; }
    #selector { height: 1fr; padding: 0 1; }
    #input-wrap { height: auto; border: solid $accent; padding: 0 1; }
    #prompt { width: 1; height: 3; color: $accent; }
    #input { width: 1fr; height: 3; border: none; }
    #statusbar { height: 1; background: $panel; color: $text; padding: 0 1; }
    .hidden { display: none; }
    """

    BINDINGS = [Binding("ctrl+c", "quit", "Quit", priority=True)]

    def __init__(self, providers: list[ProviderConfig]) -> None:
        super().__init__()
        self.providers = providers
        self.state = SessionState.SELECTING
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply = ""
        self.turn_start = 0.0
        self._stream_task: asyncio.Task[None] | None = None
        self._timer: Timer | None = None

    # ───────── 组装 ─────────
    def compose(self) -> ComposeResult:
        yield RichLog(id="log", wrap=True, markup=True)
        yield Static(id="streaming", classes="hidden")
        yield OptionList(
            *[Option(f"{p.name}  ·  {p.model}", id=str(i)) for i, p in enumerate(self.providers)],
            id="selector",
        )
        with Horizontal(id="input-wrap"):
            yield Static("❯", id="prompt")
            yield InputArea(id="input", soft_wrap=True)
        yield Static(id="statusbar")

    def on_mount(self) -> None:
        self.query_one("#log", RichLog).write(render_banner(__version__, os.getcwd()))
        # TextArea 无原生 placeholder，用输入框边框副标题承载占位提示（AC7）。
        self.query_one("#input-wrap").border_subtitle = (
            "Send a message...  (Alt+Enter 换行 · Enter 发送)"
        )
        if len(self.providers) == 1:
            self.provider = new_provider(self.providers[0])
            self.state = SessionState.IDLE
            self._update_statusbar()
        else:
            self.state = SessionState.SELECTING
        self._apply_state()

    def _apply_state(self) -> None:
        selector = self.query_one("#selector", OptionList)
        input_wrap = self.query_one("#input-wrap")
        statusbar = self.query_one("#statusbar", Static)
        streaming = self.query_one("#streaming", Static)
        if self.state == SessionState.SELECTING:
            selector.remove_class("hidden")
            input_wrap.add_class("hidden")
            statusbar.add_class("hidden")
            streaming.add_class("hidden")
            selector.focus()
        else:
            selector.add_class("hidden")
            input_wrap.remove_class("hidden")
            statusbar.remove_class("hidden")
            self.query_one("#input", InputArea).focus()

    def _update_statusbar(self) -> None:
        if self.provider is not None:
            self.query_one("#statusbar", Static).update(
                render_statusbar(self.provider.name, self.provider.model)
            )

    # ───────── 提交与流式 ─────────
    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        event.stop()
        self.submit(event.value)

    def submit(self, text: str) -> None:
        if text.strip() == "/exit":
            self._quit()
            return
        if self.state != SessionState.IDLE or self.provider is None:
            return
        self.conv.add_user(text)
        self.query_one("#log", RichLog).write(user_block(text))
        self.query_one("#input", InputArea).clear()
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.query_one("#streaming", Static).remove_class("hidden")
        self._render_streaming()
        self._stream_task = asyncio.create_task(self._consume_stream())
        self._timer = self.set_interval(0.1, self._tick)

    async def _consume_stream(self) -> None:
        assert self.provider is not None
        try:
            async for ev in self.provider.stream(self.conv.messages()):
                if ev.err is not None:
                    self._finish_with_error(ev.err)
                    return
                if ev.text:
                    self.cur_reply += ev.text
                    self._render_streaming()
                if ev.done:
                    self._finish_with_assistant(self.cur_reply)
                    return
        except asyncio.CancelledError:
            # 退出时取消流：静默，应用即将退出，不触碰 widget
            raise
        except Exception as e:  # noqa: BLE001 — 兜底，保证不中断会话
            self._finish_with_error(e)

    def _tick(self) -> None:
        if self.state == SessionState.STREAMING:
            self._render_streaming()

    def _render_streaming(self) -> None:
        elapsed = int(time.monotonic() - self.turn_start)
        self.query_one("#streaming", Static).update(streaming_view(self.cur_reply, elapsed))

    def _finish_with_assistant(self, reply: str) -> None:
        elapsed = int(time.monotonic() - self.turn_start)
        self.conv.add_assistant(reply)
        self.query_one("#log", RichLog).write(assistant_block(reply, elapsed))
        self._cleanup_streaming()
        self.state = SessionState.IDLE
        self.query_one("#input", InputArea).focus()

    def _finish_with_error(self, err: Exception) -> None:
        self.query_one("#log", RichLog).write(error_block(err))
        self._cleanup_streaming()
        self.state = SessionState.IDLE
        self.query_one("#input", InputArea).focus()

    def _cleanup_streaming(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        streaming = self.query_one("#streaming", Static)
        streaming.update("")
        streaming.add_class("hidden")

    # ───────── provider 选择 ─────────
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        idx = int(event.option.id)  # type: ignore[arg-type]
        cfg = self.providers[idx]
        self.provider = new_provider(cfg)
        self._update_statusbar()
        self.state = SessionState.IDLE
        self._apply_state()

    # ───────── 退出 ─────────
    def _quit(self) -> None:
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.exit()

    async def action_quit(self) -> None:
        self._quit()
