"""TUI App：状态机（选择/空闲/流式/待批准）、输入框、对话区、流式消费与计时、permission。

说明：plan 中将 stream.py / select.py 拆为独立文件；本实现体量较小，按
plan 允许的"可合并"条款，将流式与选择逻辑并入 ``app.py`` 以保持单会话
交互的完整可读性。历史区使用 Textual 原生可选 widget，保证拖选有高亮反馈。

ch06 扩展（权限系统）：

- ``mode`` 改用 ``permission.Mode``（四档），由注入的 ``Engine`` 决定启动模式；
  Shift+Tab 循环切换（仅 IDLE 生效，跨轮保持）。
- 新增 ``SessionState.APPROVING`` 与待批准交互态：消费 ``ApprovalRequest``
  事件后暂停事件循环，等用户三选一（↑↓ + 回车 / 数字键 1·2·3 / 便捷键 y/n/d）。
- 全局 Ctrl+C/Esc 取消分派覆盖 APPROVING（否者会退出程序）；approving 态取消
  先给 ``pending.respond`` 兜底 ``Outcome.DENY_ONCE`` 解开 agent 等待。
- 状态栏左侧常驻显示当前权限模式（取代 provider 名）。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from enum import Enum

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from koyocode import __version__
from koyocode.agent import Agent, ApprovalRequest, Phase
from koyocode.config import ProviderConfig
from koyocode.conversation import Conversation
from koyocode.llm import Provider, new_provider
from koyocode.permission import Mode, Outcome
from koyocode.prompt import EXECUTE_DIRECTIVE, render_banner
from koyocode.tool import Registry, new_default_registry

_TOOL_RESULT_MAX_LINES = 8
_COPY_FEEDBACK_TIMEOUT = 2.0
_FOLD_ARGS_LIMIT = 60
_DONE_FEEDBACK_TIMEOUT = 2.0
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SelectionPoint = tuple[int, int] | None
_SelectionFingerprint = tuple[tuple[int, _SelectionPoint, _SelectionPoint], ...]

# 待批准菜单三选项文案与 Outcome 映射（索引 0/1/2）。
_APPROVAL_OPTIONS: list[tuple[str, Outcome]] = [
    ("允许本次", Outcome.ALLOW_ONCE),
    ("永久允许（写入本地配置）", Outcome.ALLOW_FOREVER),
    ("拒绝本次", Outcome.DENY_ONCE),
]


def _fmt_tokens(n: int) -> str:
    """紧凑格式化 token 计数：千位以上显示为 ``1.2k``。"""
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def _fold_args(args: str, limit: int = _FOLD_ARGS_LIMIT) -> str:
    """工具参数超长折叠：超过 limit 字符截断并加省略号，否则原样。"""
    if len(args) > limit:
        return args[:limit] + "…"
    return args


_MODE_VISUAL: dict[Mode, tuple[str, str]] = {
    Mode.DEFAULT: ("DEFAULT", ""),  # 不染色（默认前景）
    Mode.ACCEPT_EDITS: ("ACCEPT EDITS", "#FFB347"),  # 琥珀黄
    Mode.PLAN: ("PLAN", "#4FC3F7"),  # 青蓝
    Mode.BYPASS: ("BYPASS", "#FF5252"),  # 红
}
_CYCLE_HINT = "(shift+tab to cycle)"


def next_mode(m: Mode) -> Mode:
    """Shift+Tab 循环切换：DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT。"""
    return Mode((int(m) + 1) % 4)


class SessionState(Enum):
    """会话状态：选择 provider / 等待输入 / 接收流式 / 待批准。"""

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"


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
    .turn-separator { color: $text-muted; }
    .tool-line { text-style: bold; color: cyan; }
    .tool-result { color: $text-muted; }
    .tool-error { color: $error; text-style: bold; }
    .error-message { color: $error; text-style: bold; }
    #streaming { height: auto; max-height: 12; padding: 0 1; }
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

    def __init__(
        self,
        providers: list[ProviderConfig],
        version: str = __version__,
        registry: Registry | None = None,
        engine=None,
    ) -> None:
        super().__init__()
        self.providers = providers
        self._version = version
        self.state = SessionState.SELECTING
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply = ""
        self.turn_start = 0.0
        # 权限：mode 由 engine 决定启动模式；无 engine 退化为 DEFAULT。
        self.engine = engine
        self.mode: Mode = engine.start_mode() if engine is not None else Mode.DEFAULT
        self.pending: ApprovalRequest | None = None
        self.approve_cursor: int = 0
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
        self._turn_count: int = 0
        self._spinner_frame: int = 0
        self._done_feedback_until: float | None = None
        self._done_timer: Timer | None = None

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
        self._append_history_rich(render_banner(__version__, os.getcwd()), "banner-text")
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
        # 完成态：闪现「✓ 完成」绿色，约 _DONE_FEEDBACK_TIMEOUT 后由 timer 恢复。
        if (
            self._done_feedback_until is not None
            and time.monotonic() < self._done_feedback_until
        ):
            elapsed = int(time.monotonic() - self.turn_start)
            self.query_one("#statusbar", Static).update(
                Text(f"✓ 完成 · {elapsed}s", style="green")
            )
            return
        label, color = _MODE_VISUAL[self.mode]
        usage = f"  ↑{_fmt_tokens(self.usage_in)} ↓{_fmt_tokens(self.usage_out)} tok"
        segments: list[tuple[str, str]] = [
            ("● ", color),
            (label, color),
        ]
        if self.mode != Mode.DEFAULT:
            segments.append((f" {_CYCLE_HINT}", "dim"))
        segments.append((f"    {self.provider.model}{usage}", ""))
        self.query_one("#statusbar", Static).update(Text.assemble(*segments))

    def _flash_done(self, elapsed_s: int) -> None:
        """生成完成：状态栏闪现「✓ 完成」2 秒，timer 兜底恢复常规。"""
        self._done_feedback_until = time.monotonic() + _DONE_FEEDBACK_TIMEOUT
        if self._done_timer is not None:
            self._done_timer.stop()
        self._done_timer = self.set_timer(_DONE_FEEDBACK_TIMEOUT, self._clear_done)
        self._update_statusbar()

    def _clear_done(self) -> None:
        """完成提示到期：清除完成态、恢复常规状态栏。"""
        self._done_feedback_until = None
        self._done_timer = None
        self._update_statusbar()

    def _history(self) -> VerticalScroll:
        return self.query_one("#history", VerticalScroll)

    def _scroll_history_end(self, history: VerticalScroll) -> None:
        history.scroll_end(animate=False, immediate=True, x_axis=False)

    def _scroll_history_end_deferred(self, history: VerticalScroll) -> None:
        """二层滚动：首层刷新后再调度一次，给 Markdown 异步展开留时间。"""
        self._scroll_history_end(history)
        self.call_after_refresh(self._scroll_history_end, history)

    def _append_history_widget(self, widget: Static | Markdown) -> Static | Markdown:
        history = self._history()
        history.mount(widget)
        self.call_after_refresh(self._scroll_history_end_deferred, history)
        return widget

    def _append_history_text(self, text: str, classes: str = "") -> Static:
        """追加可选文本到历史区，使用 Textual 原生 Content 参与选区。"""
        class_names = " ".join(part for part in ("history-message", classes) if part)
        widget = Static(text, classes=class_names, markup=False)
        self._append_history_widget(widget)
        return widget

    def _append_history_rich(self, text: Text, classes: str = "") -> Static:
        """追加富文本（rich.Text，带着色 span）到历史区，供 logo 等富文本使用。"""
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
            self._append_history_text("❯ /do", "user-message")
            self.mode = Mode.DEFAULT
            self.conv.add_user(EXECUTE_DIRECTIVE)
            self._update_statusbar()
            self._start_turn()
            return
        self.conv.add_user(text)
        self._append_history_text(f"❯ {text}", "user-message")
        self.query_one("#input", InputArea).clear()
        self._start_turn()

    def _start_turn(self) -> None:
        """启动一轮 Agent Loop：重置本轮状态、发起 stream task。"""
        # 非首轮先追加暗淡细线，分隔相邻回合，使每轮「query + 回复」成组。
        if self._turn_count > 0:
            self._append_history_text("─" * 40, "turn-separator")
        self._turn_count += 1
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
        """消费 ``Agent.run`` 事件流，分派文本/工具/用量/轮次/通知/审批/done/err。"""
        assert self.provider is not None
        assert self.turn_cancel is not None
        agent = Agent(self.provider, self._tool_registry, self._version, self.engine)
        finished = False
        try:
            async for ev in agent.run(self.conv, self.mode, self.turn_cancel):
                if ev.err is not None:
                    self._finish_with_error(ev.err)
                    finished = True
                    return
                if ev.approval is not None:
                    # 人在回路：切 approving 态、暂停事件循环（agent 正 await respond）
                    self.pending = ev.approval
                    self.approve_cursor = 0
                    self.state = SessionState.APPROVING
                    # 移除输入框焦点，让按键落到 App.on_key 分派待批准菜单
                    self.set_focus(None)
                    self._render_approving()
                    continue
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

    # ───────── 待批准态 ─────────
    def _render_approving(self) -> None:
        """渲染多行待批准块到 streaming 区。"""
        assert self.pending is not None
        req = self.pending
        lines: list[str] = [f"● {req.name}({req.args})"]
        if req.reason:
            lines.append(f"  {req.reason}")
        lines.append("是否继续?")
        for idx, (label, _out) in enumerate(_APPROVAL_OPTIONS):
            prefix = "> " if idx == self.approve_cursor else "  "
            lines.append(f"  {prefix}{idx + 1}. {label}")
        lines.append("  ↑↓ 选择 · 回车确认 · Esc 取消")
        self.query_one("#streaming", Static).update("\n".join(lines))

    def update_approving(self, key: str) -> None:
        """APPROVING 态按键分派：维护光标、提交决策。"""
        if self.pending is None:
            return
        if key in ("up", "k"):
            self.approve_cursor = (self.approve_cursor - 1) % len(_APPROVAL_OPTIONS)
            self._render_approving()
            return
        if key in ("down", "j"):
            self.approve_cursor = (self.approve_cursor + 1) % len(_APPROVAL_OPTIONS)
            self._render_approving()
            return
        outcome: Outcome | None = None
        if key in ("enter", "space"):
            outcome = _APPROVAL_OPTIONS[self.approve_cursor][1]
        elif key == "1":
            outcome = Outcome.ALLOW_ONCE
        elif key == "2":
            outcome = Outcome.ALLOW_FOREVER
        elif key == "3":
            outcome = Outcome.DENY_ONCE
        elif key == "y":
            outcome = Outcome.ALLOW_ONCE
        elif key in ("n", "d"):
            outcome = Outcome.DENY_ONCE
        if outcome is None:
            return
        self._submit_approval(outcome)

    def _submit_approval(self, outcome: Outcome) -> None:
        """回传用户决策给 agent（解开 await）、回到 STREAMING 继续事件循环。"""
        if self.pending is None:
            return
        respond = self.pending.respond
        self.pending = None
        self.state = SessionState.STREAMING
        self._render_streaming()
        respond.set_result(outcome)

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
        self._append_history_text(f"● {name}({_fold_args(args)})", "tool-line")
        result_class = "tool-error" if is_error else "tool-result"
        self._append_history_text(self._tool_result_text(result), result_class)
        self._render_streaming()

    def _tick(self) -> None:
        if self.state == SessionState.STREAMING:
            self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
            self._render_streaming()

    def _render_streaming(self) -> None:
        spinner = _SPINNER_FRAMES[self._spinner_frame]
        elapsed = int(time.monotonic() - self.turn_start)
        if self.cur_tools:
            view = "\n".join(
                f"{spinner} {t.name}({t.args}) · {elapsed}s" for t in self.cur_tools
            )
        else:
            round_hint = f" · 第 {self.iter} 轮" if self.iter > 0 else ""
            if self.cur_reply:
                view = f"{self.cur_reply}\n{spinner} {elapsed}s{round_hint}"
            else:
                view = f"{spinner} {elapsed}s{round_hint}"
        self.query_one("#streaming", Static).update(view)

    def _finish_turn(self, reply: str) -> None:
        """done：渲染最终答复到历史区并回到 IDLE（conv 已由 agent 写入）。"""
        elapsed = int(time.monotonic() - self.turn_start)
        if reply:
            self._append_assistant_message(reply, elapsed)
        # 最终回复（Markdown）展开后二次确认滚动到底，保证最新内容完整可见。
        self.call_after_refresh(self._scroll_history_end, self._history())
        self._flash_done(elapsed)
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

    # ───────── 全局按键 ─────────
    def on_key(self, event: events.Key) -> None:
        key = event.key
        # Shift+Tab 循环切换模式（仅 IDLE 生效）
        if key == "shift+tab" and self.state == SessionState.IDLE:
            event.stop()
            self.mode = next_mode(self.mode)
            self._update_statusbar()
            self.query_one("#input", InputArea).focus()
            return
        # APPROVING 态分派待批准按键
        if self.state == SessionState.APPROVING:
            event.stop()
            self.update_approving(key)
            return

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
        if self._done_timer is not None:
            self._done_timer.stop()
            self._done_timer = None
        self.exit()

    async def action_quit(self) -> None:
        if self._copy_selected_text():
            return
        if (
            self.state in (SessionState.STREAMING, SessionState.APPROVING)
            and self.turn_cancel is not None
        ):
            # 流式/审批态 Ctrl+C：取消本轮，不退出程序（F7）。
            if self.state == SessionState.APPROVING and self.pending is not None:
                # 兜底解开 agent 等待再取消
                self.pending.respond.set_result(Outcome.DENY_ONCE)
                self.pending = None
            self.turn_cancel.set()
            return
        self._quit()

    def action_cancel_turn(self) -> None:
        """Esc：流式/审批态取消本轮，不退出程序（F7）；其余状态忽略。"""
        if (
            self.state in (SessionState.STREAMING, SessionState.APPROVING)
            and self.turn_cancel is not None
        ):
            if self.state == SessionState.APPROVING and self.pending is not None:
                self.pending.respond.set_result(Outcome.DENY_ONCE)
                self.pending = None
            self.turn_cancel.set()


def new_app(
    providers: list[ProviderConfig],
    version: str,
    registry: Registry,
    engine,
) -> KoyoCodeApp:
    """装配 KoyoCodeApp（保持单返回，末尾增 ``engine`` 形参）。"""
    return KoyoCodeApp(providers=providers, version=version, registry=registry, engine=engine)
