"""TUI 交互单测：用 FakeProvider 驱动，无需真实 API 密钥。

用 ``asyncio.run`` 包裹 ``app.run_test()``，避免依赖 pytest-asyncio。
"""

import asyncio
import json
import os

from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Markdown, Static

import koyocode.tui.app as appmod
from koyocode.config import ProviderConfig
from koyocode.llm import StreamEvent, ToolCall
from koyocode.permission import Mode, Outcome
from koyocode.permission import new_engine
from koyocode.tui import KoyoCodeApp, SessionState


def _engine():
    """根于 cwd 的权限引擎（TUI 注入用，降级不抛）。"""
    e, _ = new_engine(os.getcwd())
    return e


class FakeProvider:
    """协议无关的假 provider，按预设事件序列吐出。"""

    def __init__(self, name="Fake", model="fake-1", events=None):
        self._name = name
        self._model = model
        self._events = events or []

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    async def stream(self, req):  # type: ignore[no-untyped-def]
        for e in self._events:
            await asyncio.sleep(0)
            yield e


def _provider_cfg(name="Fake", model="fake-1"):
    return ProviderConfig(name=name, protocol="anthropic", api_key="k", model=model)


def _run(coro):
    return asyncio.run(coro)


def _selected_text(widget, start, end):
    selection = Selection(start, end)
    result = widget.get_selection(selection)
    assert result is not None
    return result[0]


def test_history_uses_selectable_native_widgets(monkeypatch):
    """历史区使用原生可选 widget，banner 可被 Textual 选区抽取。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            banner = history.query_one(".banner-text", Static)

            selected = banner.get_selection(Selection(None, None))

            assert selected is not None
            assert "koyoCode" in selected[0]

    _run(run())


def test_streaming_text_is_partially_selectable(monkeypatch):
    """流式输出区应可按字符选中一部分回复文本。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.cur_reply = "stream partial text"
            app.turn_start = 0.0
            app._render_streaming()
            streaming = app.query_one("#streaming", Static)

            assert _selected_text(streaming, Offset(7, 0), Offset(14, 0)) == "partial"

    _run(run())


def test_statusbar_text_is_selectable(monkeypatch):
    """状态栏 provider/model 文本也应进入 Textual 选区系统。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            statusbar = app.query_one("#statusbar", Static)
            selected = statusbar.get_selection(Selection(None, None))

            assert selected is not None
            # 左侧常驻权限模式 DEFAULT（取代 provider 名），右侧仍含 model 名
            assert "DEFAULT" in selected[0]
            assert "fake-1" in selected[0]

    _run(run())


def test_completed_assistant_reply_uses_textual_markdown(monkeypatch):
    """完成后的助手回复进入历史区，并由 Textual Markdown widget 承载。"""
    fake = FakeProvider(
        events=[
            StreamEvent(text="Hello **world**"),
            StreamEvent(done=True),
        ]
    )
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", appmod.InputArea)
            inp.text = "hi"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            await pilot.pause()

            history = app.query_one("#history", VerticalScroll)
            markdown = history.query_one(".assistant-message", Markdown)

            assert markdown.source == "Hello **world**"

    _run(run())


def test_single_provider_enters_idle(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(text="Hi!"), StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.state == SessionState.IDLE
            assert app.provider is fake

    _run(run())


def test_submit_and_stream_flow(monkeypatch):
    fake = FakeProvider(
        events=[
            StreamEvent(text="Hello"),
            StreamEvent(text=" world"),
            StreamEvent(done=True),
        ]
    )
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", appmod.InputArea)
            inp.text = "hi"
            await pilot.pause()
            await pilot.press("enter")
            # 等待流式 task 完成
            for _ in range(20):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.state == SessionState.IDLE
            msgs = app.conv.messages()
            assert [m.role for m in msgs] == ["user", "assistant"]
            assert msgs[0].content == "hi"
            assert msgs[1].content == "Hello world"

    _run(run())


def test_error_event_keeps_session_alive(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(err=RuntimeError("boom"))])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", appmod.InputArea)
            inp.text = "go"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            # 错误后回到 IDLE，未崩溃，可继续
            assert app.state == SessionState.IDLE
            # 助手消息未入历史
            assert [m.role for m in app.conv.messages()] == ["user"]

    _run(run())


def test_multi_provider_selection(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(text="ok"), StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp(
            [
                _provider_cfg("A", "model-a"),
                _provider_cfg("B", "model-b"),
            ],
            engine=_engine(),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.state == SessionState.SELECTING
            assert app.provider is None
            # 选择第二项（下移 + Enter）
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert app.state == SessionState.IDLE
            assert app.provider is fake

    _run(run())


def test_ctrl_c_triggers_quit(monkeypatch):
    """无选区时 Ctrl+C 在输入框聚焦应触发退出。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        called = {"v": False}

        def fake_quit():
            called["v"] = True

        app.action_quit = fake_quit  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            # 输入框聚焦时按 Ctrl+C
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert called["v"], "ctrl+c 未触发 action_quit（可能被 TextArea 复制截获）"

    _run(run())


def test_ctrl_c_copies_selection_without_quitting(monkeypatch):
    """有选区时 Ctrl+C 复制选区，不退出应用。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        copied = []
        called = {"quit": False}

        def fake_quit():
            called["quit"] = True

        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        app.notify = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("复制反馈不应使用弹框通知")
        )
        app._quit = fake_quit  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            message = Static("copy me", classes="history-message")
            await history.mount(message)
            await pilot.pause()
            app.screen.selections = {message: Selection(Offset(0, 0), Offset(7, 0))}

            await pilot.press("ctrl+c")
            await pilot.pause()

            assert copied == ["copy me"]
            feedback = app.query_one("#copy-feedback", Static)
            assert feedback.content == "copied 7 chars to clipboard"
            assert "hidden" not in feedback.classes
            assert called["quit"] is False

    _run(run())


def test_copy_selected_text_shows_feedback_once_for_same_selection(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        copied = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        app.notify = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("复制反馈不应使用弹框通知")
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            message = Static("abcdef", classes="history-message")
            await history.mount(message)
            await pilot.pause()
            app.screen.selections = {message: Selection(Offset(0, 0), Offset(6, 0))}

            assert app._copy_selected_text() is True
            assert app._copy_selected_text() is True

            assert copied == ["abcdef"]
            feedback = app.query_one("#copy-feedback", Static)
            assert feedback.content == "copied 6 chars to clipboard"
            assert "hidden" not in feedback.classes

    _run(run())


def test_copy_selected_text_recopies_same_text_from_different_selection(monkeypatch):
    """文本相同但选区不同，仍应按用户的新复制动作写入剪贴板。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        copied = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            first = Static("same", classes="history-message")
            second = Static("same", classes="history-message")
            await history.mount(first)
            await history.mount(second)
            await pilot.pause()

            app.screen.selections = {first: Selection(Offset(0, 0), Offset(4, 0))}
            assert app._copy_selected_text() is True

            app.screen.selections = {second: Selection(Offset(0, 0), Offset(4, 0))}
            assert app._copy_selected_text() is True

            assert copied == ["same", "same"]

    _run(run())


def test_mouse_up_copies_selection_and_stops_event(monkeypatch):
    """鼠标松开后若存在有效选区，应自动复制并停止事件继续冒泡。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    class FakeMouseUp:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        copied = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            message = Static("drag copy", classes="history-message")
            await history.mount(message)
            await pilot.pause()
            app.screen.selections = {message: Selection(Offset(0, 0), Offset(9, 0))}

            event = FakeMouseUp()
            app.on_mouse_up(event)  # type: ignore[arg-type]

            assert copied == ["drag copy"]
            assert event.stopped is True

    _run(run())


def test_copy_selected_text_ignores_out_of_range_selection(monkeypatch):
    """Textual 可能给出越界选区，复制逻辑不应因此崩溃。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        copied = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            history = app.query_one("#history", VerticalScroll)
            message = Static("one line", classes="history-message")
            await history.mount(message)
            await pilot.pause()
            app.screen.selections = {message: Selection(Offset(0, 1), Offset(1, 1))}

            assert app._copy_selected_text() is False

            feedback = app.query_one("#copy-feedback", Static)
            assert copied == []
            assert feedback.content == ""
            assert "hidden" not in feedback.classes

    _run(run())


def test_clear_copy_feedback_clears_inline_message(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_copy_feedback(4)

            app._clear_copy_feedback()

            feedback = app.query_one("#copy-feedback", Static)
            assert feedback.content == ""
            assert "hidden" not in feedback.classes
            assert app._copy_feedback_timer is None

    _run(run())


def test_copy_feedback_sits_above_input_wrap(monkeypatch):
    """复制反馈应占据输入框上方独立一行，而不是叠在输入框内。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_copy_feedback(3)
            await pilot.pause()

            feedback = app.query_one("#copy-feedback", Static)
            input_wrap = app.query_one("#input-wrap")

            assert feedback.region.y == input_wrap.region.y - 1
            assert feedback.region.height == 1
            assert "hidden" not in feedback.classes

    _run(run())


def test_copy_feedback_replaces_previous_hide_timer(monkeypatch):
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    class FakeTimer:
        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback
            self.stopped = False

        def stop(self):
            self.stopped = True

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        timers = []

        def fake_set_timer(delay, callback):
            timer = FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        app.set_timer = fake_set_timer  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()

            app._show_copy_feedback(1)
            app._show_copy_feedback(2)

            feedback = app.query_one("#copy-feedback", Static)
            assert feedback.content == "copied 2 chars to clipboard"
            assert [timer.delay for timer in timers] == [
                appmod._COPY_FEEDBACK_TIMEOUT,
                appmod._COPY_FEEDBACK_TIMEOUT,
            ]
            assert timers[0].stopped is True
            assert timers[1].stopped is False
            assert app._copy_feedback_timer is timers[1]

    _run(run())


def test_alt_enter_inserts_newline(monkeypatch):
    """Alt+Enter 应插入换行而非提交。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", appmod.InputArea)
            inp.clear()
            inp.insert("ab")
            await pilot.pause()
            await pilot.press("alt+enter")
            await pilot.pause()
            assert "\n" in inp.text, f"Alt+Enter 未插入换行，input={inp.text!r}"
            # 未提交：仍处于 IDLE
            assert app.state == SessionState.IDLE

    _run(run())


class _ScriptedFakeProvider:
    """按调用次序依次吐出预设脚本（支持多轮：工具调用轮 + 续答轮）。"""

    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0

    @property
    def name(self):
        return "Fake"

    @property
    def model(self):
        return "fake-1"

    async def stream(self, req):  # type: ignore[no-untyped-def]
        script = self._scripts[self._i]
        self._i += 1
        for e in script:
            await asyncio.sleep(0)
            yield e


def test_tool_call_turn_renders_and_round_trips(monkeypatch):
    """AC8（TUI 级）：工具调用轮经 App 完整跑通——历史含 tool 回合且回到 IDLE。"""
    from pathlib import Path

    target = Path(__file__).resolve().parent.parent / "pyproject.toml"
    fake = _ScriptedFakeProvider(
        scripts=[
            [
                StreamEvent(text="我先读取该文件"),
                StreamEvent(
                    tool_calls=[
                        ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(target)}))
                    ]
                ),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="已读取并总结完毕"),
                StreamEvent(done=True),
            ],
        ]
    )
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", appmod.InputArea)
            inp.text = "读 pyproject.toml 并总结"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.state == SessionState.IDLE
            msgs = app.conv.messages()
            # [user, assistant(tool_calls), tool, assistant(最终文本)]
            assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
            assert msgs[1].tool_calls[0].name == "read_file"
            assert msgs[2].tool_results[0].is_error is False
            assert msgs[3].content == "已读取并总结完毕"

    _run(run())


# ───────── 权限系统：Shift+Tab / 待批准态（T11）─────────


def test_shift_tab_cycles_modes(monkeypatch):
    """IDLE 态连按 Shift+Tab 依次循环四档、停留 IDLE。"""
    from koyocode.permission import Mode

    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.state == SessionState.IDLE
            order = [Mode.DEFAULT]
            for _ in range(4):
                await pilot.press("shift+tab")
                await pilot.pause()
                order.append(app.mode)
            # 循环 DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT
            assert order == [Mode.DEFAULT, Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS, Mode.DEFAULT]
            assert app.state == SessionState.IDLE  # 全程停留 IDLE

    _run(run())


def test_status_bar_shows_current_mode_no_provider_name(monkeypatch):
    """状态栏左侧在各模式显示模式名，且不含 provider 名。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg("SomeProviderName", "model-x")], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            sb = app.query_one("#statusbar", Static).content
            assert "DEFAULT" in sb
            assert "SomeProviderName" not in sb  # 不再显示 provider 名
            assert "fake-1" in sb  # 右侧模型名保留

            await pilot.press("shift+tab")
            await pilot.pause()
            assert "ACCEPT EDITS" in app.query_one("#statusbar", Static).content

            await pilot.press("shift+tab")
            await pilot.pause()
            assert "PLAN" in app.query_one("#statusbar", Static).content

            await pilot.press("shift+tab")
            await pilot.pause()
            assert "BYPASS" in app.query_one("#statusbar", Static).content

    _run(run())


def test_mode_persists_across_turns(monkeypatch):
    """Shift+Tab 切到 ACCEPT_EDITS 后再 begin_turn，app.mode 仍为 ACCEPT_EDITS。"""
    fake = FakeProvider(events=[StreamEvent(text="ok"), StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.mode == Mode.ACCEPT_EDITS
            # 一次完整 turn
            inp = app.query_one("#input", appmod.InputArea)
            inp.text = "hi"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.state == SessionState.IDLE
            # mode 跨轮保持，未被重置
            assert app.mode == Mode.ACCEPT_EDITS

    _run(run())


def test_approval_request_flows_to_approving(monkeypatch, tmp_path):
    """default 下 write_file 触发 ApprovalRequest -> 切 APPROVING、pending 已设、cursor=0。"""
    target = str(tmp_path / "to_approve.txt")
    engine = new_engine(str(tmp_path))[0]

    class _ApprovalProvider:
        name = "Fake"
        model = "fake-1"

        def __init__(self):
            self._first = True

        async def stream(self, req):
            if self._first:
                self._first = False
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(id="a1", name="write_file", input=json.dumps({"path": target, "content": "x"}))
                    ]
                )
                yield StreamEvent(done=True)
            else:
                yield StreamEvent(text="ok")
                yield StreamEvent(done=True)

    provider = _ApprovalProvider()
    app = KoyoCodeApp([_provider_cfg()], engine=engine)

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            # on_mount 已用 new_provider 设置过 provider，这里旁路覆盖为脚本 provider
            app.provider = provider
            app.state = SessionState.IDLE
            await pilot.pause()
            # 直接触发一轮
            app.conv.add_user("写文件")
            # 调 _start_turn 模拟一次提交
            app._start_turn()
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.APPROVING:
                    break
            assert app.state == SessionState.APPROVING
            assert app.pending is not None
            assert app.pending.name == "write_file"
            assert app.approve_cursor == 0

    _run(run())


def test_approval_down_enter_returns_allow_forever(monkeypatch, tmp_path):
    """APPROVING: 按 down 移到第 2 项、回车 -> respond 收到 ALLOW_FOREVER，回 STREAMING。"""
    target = str(tmp_path / "af.txt")
    engine = new_engine(str(tmp_path))[0]
    fut_holder: dict = {}

    class _ApprovalProvider:
        name = "Fake"
        model = "fake-1"

        def __init__(self):
            self._i = 0

        async def stream(self, req):
            self._i += 1
            if self._i == 1:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(id="a1", name="write_file", input=json.dumps({"path": target, "content": "x"}))
                    ]
                )
                yield StreamEvent(done=True)
            else:
                yield StreamEvent(text="done")
                yield StreamEvent(done=True)

    provider = _ApprovalProvider()

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.provider = provider
            app.state = SessionState.IDLE
            await pilot.pause()
            app.conv.add_user("写文件")
            app._start_turn()
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.APPROVING:
                    break
            # down -> cursor 1（ALLOW_FOREVER），enter 提交
            await pilot.press("down")
            await pilot.pause()
            assert app.approve_cursor == 1
            fut = app.pending.respond
            await pilot.press("enter")
            await pilot.pause()
            assert fut.done() and fut.result() == Outcome.ALLOW_FOREVER
            # 提交后离开 APPROVING（STREAMING 继续直至 IDLE）
            assert app.state != SessionState.APPROVING
            # 等待完成
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.state == SessionState.IDLE
            assert (tmp_path / "af.txt").exists()

    _run(run())


def test_approval_number_keys(monkeypatch, tmp_path):
    """数字键 1 -> ALLOW_ONCE、3 -> DENY_ONCE 直选。"""
    engine = new_engine(str(tmp_path))[0]

    class _ApprovalProvider:
        name = "Fake"
        model = "fake-1"

        def __init__(self):
            self._i = 0

        async def stream(self, req):
            self._i += 1
            if self._i == 1:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(id="a1", name="write_file", input=json.dumps({"path": str(tmp_path / "n1.txt"), "content": "x"}))
                    ]
                )
                yield StreamEvent(done=True)
            else:
                yield StreamEvent(text="done")
                yield StreamEvent(done=True)

    async def run():
        provider = _ApprovalProvider()
        app = KoyoCodeApp([_provider_cfg()], engine=engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.provider = provider
            app.state = SessionState.IDLE
            await pilot.pause()
            app.conv.add_user("写文件")
            app._start_turn()
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.APPROVING:
                    break
            fut = app.pending.respond
            await pilot.press("1")
            await pilot.pause()
            assert fut.done() and fut.result() == Outcome.ALLOW_ONCE

    _run(run())


def test_approval_escape_cancels_with_deny_once(monkeypatch, tmp_path):
    """APPROVING 态按 Esc：兜底 respond DENY_ONCE、应用未退出、本轮被取消。"""
    engine = new_engine(str(tmp_path))[0]

    class _ApprovalProvider:
        name = "Fake"
        model = "fake-1"

        def __init__(self):
            self._i = 0

        async def stream(self, req):
            self._i += 1
            if self._i == 1:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(id="a1", name="write_file", input=json.dumps({"path": str(tmp_path / "esc.txt"), "content": "x"}))
                    ]
                )
                yield StreamEvent(done=True)
            else:
                yield StreamEvent(text="done")
                yield StreamEvent(done=True)

    async def run():
        provider = _ApprovalProvider()
        app = KoyoCodeApp([_provider_cfg()], engine=engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.provider = provider
            app.state = SessionState.IDLE
            await pilot.pause()
            app.conv.add_user("写文件")
            app._start_turn()
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.APPROVING:
                    break
            fut = app.pending.respond
            await pilot.press("escape")
            await pilot.pause()
            # 兜底解开 agent 等待
            assert fut.done() and fut.result() == Outcome.DENY_ONCE
            # 应用未退出（仍可继续交互）
            assert app._running
            # 等收尾回 IDLE
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.state == SessionState.IDLE
            assert not (tmp_path / "esc.txt").exists()

    _run(run())
