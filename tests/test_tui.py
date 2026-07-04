"""TUI 交互单测：用 FakeProvider 驱动，无需真实 API 密钥。

用 ``asyncio.run`` 包裹 ``app.run_test()``，避免依赖 pytest-asyncio。
"""

import asyncio

import koyocode.tui.app as appmod
from koyocode.config import ProviderConfig
from koyocode.llm import StreamEvent
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

    async def stream(self, msgs):  # type: ignore[no-untyped-def]
        for e in self._events:
            await asyncio.sleep(0)
            yield e


def _provider_cfg(name="Fake", model="fake-1"):
    return ProviderConfig(name=name, protocol="anthropic", api_key="k", model=model)


def _run(coro):
    return asyncio.run(coro)


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
    """Ctrl+C 在输入框聚焦时应触发 action_quit，而非被 TextArea 复制截获。"""
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
