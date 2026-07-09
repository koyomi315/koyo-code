"""TUI 交互单测：用 FakeProvider 驱动，无需真实 API 密钥。

用 ``asyncio.run`` 包裹 ``app.run_test()``，避免依赖 pytest-asyncio。
"""

import asyncio
import json

from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Markdown, Static

import koyocode.tui.app as appmod
from koyocode.config import ProviderConfig
from koyocode.llm import StreamEvent, ToolCall
from koyocode.tui import KoyoCodeApp, SessionState


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

    async def stream(self, msgs, tools):  # type: ignore[no-untyped-def]
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
        async with app.run_test() as pilot:
            await pilot.pause()
            statusbar = app.query_one("#statusbar", Static)
            selected = statusbar.get_selection(Selection(None, None))

            assert selected is not None
            assert "Fake" in selected[0]
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
            ]
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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
        app = KoyoCodeApp([_provider_cfg()])
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

    async def stream(self, msgs, tools):  # type: ignore[no-untyped-def]
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
        app = KoyoCodeApp([_provider_cfg()])
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
