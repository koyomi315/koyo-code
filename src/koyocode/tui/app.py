"""TUI App：状态机（选择/空闲/流式）、输入框、对话区、流式消费与计时、provider 选择。

说明：plan 中将 stream.py / select.py 拆为独立文件；本实现体量较小，按
plan 允许的"可合并"条款，将流式与选择逻辑并入 ``app.py`` 以保持单会话
交互的完整可读性。历史区使用 Textual 原生可选 widget，保证拖选有高亮反馈。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from enum import Enum

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from koyocode import __version__
from koyocode.agent import Agent, Mode, Phase
from koyocode.config import ProviderConfig
from koyocode.conversation import Conversation
from koyocode.llm import Provider, new_provider
from koyocode.prompt import EXECUTE_DIRECTIVE, render_banner
from koyocode.tool import Registry, new_default_registry

_TOOL_RESULT_MAX_LINES = 8
_COPY_FEEDBACK_TIMEOUT = 2.0
_SelectionPoint = tuple[int, int] | None
_SelectionFingerprint = tuple[tuple[int, _SelectionPoint, _SelectionPoint], ...]


def _fmt_tokens(n: int) -> str:
    """紧凑格式化 token 计数：千位以上显示为 ``1.2k``。"""
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


class SessionState(Enum):
    """会话状态：选择 provider / 等待输入 / 接收流式。"""

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"


@dataclass
class ToolDisplay:
    """当前执行中工具的展示状态（动态区 Running 指示）。"""

    name: str
    args: str


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
    #history { height: 1fr; padding: 0 1; overflow-y: auto; }
    .history-message { width: 1fr; height: auto; }
    .banner-text { color: $text-muted; }
    .user-message { text-style: bold; }
    .assistant-marker { color: cyan; }
    .assistant-message { padding: 0; margin: 0 0 1 0; }
    .elapsed-line { color: $text-muted; }
    .notice-message { color: $text-muted; }
    .tool-line { text-style: bold; color: cyan; }
    .tool-result { color: $text-muted; }
    .tool-error { color: $error; text-style: bold; }
    .error-message { color: $error; text-style: bold; }
    #streaming { height: auto; max-height: 8; padding: 0 1; }
    #selector { height: 1fr; padding: 0 1; }
    #input-wrap { height: auto; border: solid $accent; padding: 0 1; }
    #prompt { width: 1; height: 3; color: $accent; }
    #input { width: 1fr; height: 3; border: none; }
    #statusbar { height: 1; background: $panel; color: $text; padding: 0 1; }
    #copy-feedback {
        width: 100%;
        height: 1;
        padding: 0 1;
        color: #b8c7ff;
        background: transparent;
        content-align: right middle;
    }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("escape", "cancel_turn", "Cancel", show=False, priority=True),
    ]

    def __init__(self, providers: list[ProviderConfig], registry: Registry | None = None) -> None:
        super().__init__()
        self.providers = providers
        self.state = SessionState.SELECTING
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply = ""
        self.turn_start = 0.0
        self.mode: Mode = Mode.NORMAL
        self.iter = 0
        self.usage_in = 0
        self.usage_out = 0
        self.turn_cancel: asyncio.Event | None = None
        self._tool_registry: Registry = registry or new_default_registry()
        self.cur_tools: list[ToolDisplay] = []
        self._stream_task: asyncio.Task[None] | None = None
        self._timer: Timer | None = None
        self._copy_feedback_timer: Timer | None = None
        self._last_copied_selection: _SelectionFingerprint | None = None

    # ───────── 组装 ─────────
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="history")
        yield Static(id="streaming", classes="hidden", markup=False)
        yield OptionList(
            *[Option(f"{p.name}  ·  {p.model}", id=str(i)) for i, p in enumerate(self.providers)],
            id="selector",
        )
        yield Static("", id="copy-feedback", classes="hidden", markup=False)
        with Horizontal(id="input-wrap"):
            yield Static("❯", id="prompt")
            yield InputArea(id="input", soft_wrap=True)
        yield Static(id="statusbar", markup=False)

    def on_mount(self) -> None:
        self._append_history_text(render_banner(__version__, os.getcwd()), "banner-text")
        # TextArea 无原生 placeholder，用输入框边框副标题承载占位提示（AC7）。
        self.query_one(
            "#input-wrap"
        ).border_subtitle = "Send a message...  (Alt+Enter 换行 · Enter 发送)"
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
        feedback = self.query_one("#copy-feedback", Static)
        if self.state == SessionState.SELECTING:
            selector.remove_class("hidden")
            input_wrap.add_class("hidden")
            statusbar.add_class("hidden")
            streaming.add_class("hidden")
            feedback.add_class("hidden")
            selector.focus()
        else:
            selector.add_class("hidden")
            input_wrap.remove_class("hidden")
            statusbar.remove_class("hidden")
            feedback.remove_class("hidden")
            self.query_one("#input", InputArea).focus()

    def _update_statusbar(self) -> None:
        if self.provider is None:
            return
        mode_badge = "  [PLAN]" if self.mode == Mode.PLAN else ""
        usage = f"  ↑{_fmt_tokens(self.usage_in)} ↓{_fmt_tokens(self.usage_out)} tok"
        self.query_one("#statusbar", Static).update(
            f"● {self.provider.name}{mode_badge}    {self.provider.model}{usage}"
        )

    def _history(self) -> VerticalScroll:
        return self.query_one("#history", VerticalScroll)

    def _scroll_history_end(self, history: VerticalScroll) -> None:
        history.scroll_end(animate=False, immediate=True, x_axis=False)

    def _append_history_widget(self, widget: Static | Markdown) -> Static | Markdown:
        history = self._history()
        history.mount(widget)
        self.call_after_refresh(self._scroll_history_end, history)
        return widget

    def _append_history_text(self, text: str, classes: str = "") -> Static:
        """追加可选文本到历史区，使用 Textual 原生 Content 参与选区。"""
        class_names = " ".join(part for part in ("history-message", classes) if part)
        widget = Static(text, classes=class_names, markup=False)
        self._append_history_widget(widget)
        return widget

    def _append_assistant_message(self, reply: str, elapsed_s: int | None = None) -> Markdown:
        """追加助手 Markdown 回复，并保留独立圆点/耗时文本可选。"""
        self._append_history_text("●", "assistant-marker")
        markdown = Markdown(reply, classes="history-message assistant-message")
        self._append_history_widget(markdown)
        if elapsed_s is not None:
            self._append_history_text(f"  √ {elapsed_s}s", "elapsed-line")
        return markdown

    def _tool_result_text(self, result: str) -> str:
        lines = result.splitlines()
        if len(lines) > _TOOL_RESULT_MAX_LINES:
            lines = [*lines[:_TOOL_RESULT_MAX_LINES], "[...]"]
        if not lines:
            return "  └ "
        body = "\n    ".join(lines)
        return f"  └ {body}"

    def _selection_fingerprint(self) -> _SelectionFingerprint:
        def point(value: object) -> _SelectionPoint:
            if value is None:
                return None
            return (value.x, value.y)  # type: ignore[attr-defined]

        return tuple(
            (id(widget), point(selection.start), point(selection.end))
            for widget, selection in sorted(
                self.screen.selections.items(), key=lambda item: id(item[0])
            )
        )

    def _reset_last_copied_selection(self) -> None:
        self._last_copied_selection = None

    # ───────── 选区复制 ─────────
    def _copy_selected_text(self) -> bool:
        fingerprint = self._selection_fingerprint()
        try:
            text = self.screen.get_selected_text()
        except IndexError:
            self._reset_last_copied_selection()
            return False
        if text is None:
            self._reset_last_copied_selection()
            return False
        text = text.rstrip("\n")
        if not text:
            self._reset_last_copied_selection()
            return False
        if fingerprint and fingerprint == self._last_copied_selection:
            return True
        self.copy_to_clipboard(text)
        self._show_copy_feedback(len(text))
        self._last_copied_selection = fingerprint
        return True

    def _show_copy_feedback(self, copied_chars: int) -> None:
        feedback = self.query_one("#copy-feedback", Static)
        feedback.update(f"copied {copied_chars} chars to clipboard")
        feedback.remove_class("hidden")
        if self._copy_feedback_timer is not None:
            self._copy_feedback_timer.stop()
        self._copy_feedback_timer = self.set_timer(
            _COPY_FEEDBACK_TIMEOUT, self._clear_copy_feedback
        )

    def _clear_copy_feedback(self) -> None:
        self._copy_feedback_timer = None
        feedback = self.query_one("#copy-feedback", Static)
        feedback.update("")

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._copy_selected_text():
            event.stop()

    # ───────── 提交与流式 ─────────
    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        event.stop()
        self.submit(event.value)

    def submit(self, text: str) -> None:
        stripped = text.strip()
        if stripped == "/exit":
            self._quit()
            return
        if self.state != SessionState.IDLE or self.provider is None:
            return
        if stripped == "/plan":
            self.query_one("#input", InputArea).clear()
            self.mode = Mode.PLAN
            self._append_history_text(
                "● 已进入计划模式（仅只读工具，/do 切回执行）", "notice-message"
            )
            self._update_statusbar()
            return
        if stripped == "/do":
            self.query_one("#input", InputArea).clear()
            self._append_history_text("● /do", "user-message")
            self.mode = Mode.NORMAL
            self.conv.add_user(EXECUTE_DIRECTIVE)
            self._update_statusbar()
            self._start_turn()
            return
        self.conv.add_user(text)
        self._append_history_text(f"● {text}", "user-message")
        self.query_one("#input", InputArea).clear()
        self._start_turn()

    def _start_turn(self) -> None:
        """启动一轮 Agent Loop：重置本轮状态、发起 stream task。"""
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_start = time.monotonic()
        self.turn_cancel = asyncio.Event()
        self.state = SessionState.STREAMING
        self.query_one("#streaming", Static).remove_class("hidden")
        self._render_streaming()
        self._stream_task = asyncio.create_task(self._consume_agent_events())
        self._timer = self.set_interval(0.1, self._tick)

    async def _consume_agent_events(self) -> None:
        """消费 ``Agent.run`` 事件流，分派文本/工具/用量/轮次/通知/done/err 到 UI 与历史。"""
        assert self.provider is not None
        assert self.turn_cancel is not None
        agent = Agent(self.provider, self._tool_registry)
        finished = False
        try:
            async for ev in agent.run(self.conv, self.mode, self.turn_cancel):
                if ev.err is not None:
                    self._finish_with_error(ev.err)
                    finished = True
                    return
                if ev.tool is not None:
                    if ev.tool.phase == Phase.START:
                        self._on_tool_start(ev.tool.name, ev.tool.args)
                    else:
                        self._on_tool_end(
                            ev.tool.name, ev.tool.args, ev.tool.result, ev.tool.is_error
                        )
                    continue
                if ev.usage is not None:
                    self.usage_in += ev.usage.input
                    self.usage_out += ev.usage.output
                    self._update_statusbar()
                if ev.notice:
                    self._append_history_text(f"● {ev.notice}", "notice-message")
                if ev.iter:
                    self.iter = ev.iter
                    self._render_streaming()
                if ev.text:
                    self.cur_reply += ev.text
                    self._render_streaming()
                if ev.done:
                    # agent 已把最终答复写入 conv；此处仅渲染到历史区。
                    self._finish_turn(self.cur_reply)
                    finished = True
                    return
        except asyncio.CancelledError:
            # 退出时取消流：静默，应用即将退出，不触碰 widget。
            finished = True
            raise
        except Exception as e:  # noqa: BLE001 — 兜底，保证不中断会话
            self._finish_with_error(e)
            finished = True
        finally:
            if not finished:
                # 用户取消：generator 未发 done 即终止，此处仍需收尾回到 IDLE。
                self._finish_turn(self.cur_reply)

    def _on_tool_start(self, name: str, args: str) -> None:
        """工具开始：先提交 preamble 文本到滚动历史，再加入 Running 指示队列。"""
        if self.cur_reply:
            self._append_assistant_message(self.cur_reply)
            self.cur_reply = ""
        self.cur_tools.append(ToolDisplay(name=name, args=args))
        self._render_streaming()

    def _on_tool_end(self, name: str, args: str, result: str, is_error: bool) -> None:
        """工具结束：写工具行 + 结果摘要到滚动历史，从 Running 队首弹出。"""
        if self.cur_tools:
            self.cur_tools.pop(0)
        self._append_history_text(f"● {name}({args})", "tool-line")
        result_class = "tool-error" if is_error else "tool-result"
        self._append_history_text(self._tool_result_text(result), result_class)
        self._render_streaming()

    def _tick(self) -> None:
        if self.state == SessionState.STREAMING:
            self._render_streaming()

    def _render_streaming(self) -> None:
        elapsed = int(time.monotonic() - self.turn_start)
        if self.cur_tools:
            view = "\n".join(
                f"● {t.name}({t.args}) Running... ({elapsed}s)" for t in self.cur_tools
            )
        else:
            round_hint = f" · 第 {self.iter} 轮" if self.iter > 0 else ""
            if self.cur_reply:
                view = f"{self.cur_reply}\nImagining... ({elapsed}s{round_hint})"
            else:
                view = f"Imagining... ({elapsed}s{round_hint})"
        self.query_one("#streaming", Static).update(view)

    def _finish_turn(self, reply: str) -> None:
        """done：渲染最终答复到历史区并回到 IDLE（conv 已由 agent 写入）。"""
        elapsed = int(time.monotonic() - self.turn_start)
        if reply:
            self._append_assistant_message(reply, elapsed)
        self._cleanup_streaming()
        self.state = SessionState.IDLE
        self.query_one("#input", InputArea).focus()

    def _finish_with_error(self, err: Exception) -> None:
        self._append_history_text(f"● {err}", "error-message")
        self._cleanup_streaming()
        self.state = SessionState.IDLE
        self.query_one("#input", InputArea).focus()

    def _cleanup_streaming(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
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
        if self._copy_feedback_timer is not None:
            self._copy_feedback_timer.stop()
            self._copy_feedback_timer = None
        self.exit()

    async def action_quit(self) -> None:
        if self._copy_selected_text():
            return
        if self.state == SessionState.STREAMING and self.turn_cancel is not None:
            # 流式态 Ctrl+C：取消本轮，不退出程序（F7）。
            self.turn_cancel.set()
            return
        self._quit()

    def action_cancel_turn(self) -> None:
        """Esc：流式态取消本轮，不退出程序（F7）；其余状态忽略。"""
        if self.state == SessionState.STREAMING and self.turn_cancel is not None:
            self.turn_cancel.set()
