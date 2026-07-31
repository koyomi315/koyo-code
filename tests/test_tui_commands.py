"""TUI 斜杠命令分发单测（ch08）：/compact 路由、/unknown 提示、/exit /plan /do 迁移。

用 ``MockAgent`` 验证 /compact 不走 LLM 主对话路径；用 ``FakeProvider`` 验证
迁移后的 /plan /do /exit 行为不回归。
"""

from __future__ import annotations

import asyncio
import os

from textual.widgets import Static

import koyocode.tui.app as appmod
from koyocode.agent import Event
from koyocode.config import ProviderConfig
from koyocode.llm import StreamEvent
from koyocode.permission import Mode, new_engine
from koyocode.prompt import EXECUTE_DIRECTIVE
from koyocode.tui import KoyoCodeApp, SessionState


def _engine():
    e, _ = new_engine(os.getcwd())
    return e


def _provider_cfg(name="Fake", model="fake-1"):
    return ProviderConfig(name=name, protocol="anthropic", api_key="k", model=model)


class FakeProvider:
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


class MockAgent:
    """记录 run_force_compact / run 调用次数的 mock agent。"""

    def __init__(self, compact_result=(120000, 42000)):
        self.run_force_compact_calls = 0
        self.run_calls = 0
        self._compact_result = compact_result

    async def run_force_compact(self, conv, tool_defs):  # type: ignore[no-untyped-def]
        self.run_force_compact_calls += 1
        return self._compact_result

    async def run(self, conv, mode, cancel):  # type: ignore[no-untyped-def]
        self.run_calls += 1
        yield Event(done=True)


def _run(coro):
    return asyncio.run(coro)


def _history_texts(app):
    return [str(w.content) for w in app.query_one("#history").query(Static)]


def test_tui_slash_compact_routes_to_command(monkeypatch):
    """/compact 走命令路径，调 run_force_compact，不调 run（不发 LLM 主对话）。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            mock = MockAgent()
            app.agent = mock
            app._agent_provider = app.provider  # 防止 _ensure_agent 重建
            app.query_one("#input", appmod.InputArea).text = "/compact"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if mock.run_force_compact_calls > 0:
                    break
            assert mock.run_force_compact_calls == 1
            assert mock.run_calls == 0
            texts = _history_texts(app)
            assert any("已压缩，token 从 120000 降至 42000" in t for t in texts)

    _run(run())


def test_tui_slash_compact_low_token_still_works(monkeypatch):
    """手动 /compact 无视阈值：低 token 也能压缩（覆盖 AC13）。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            mock = MockAgent(compact_result=(500, 300))
            app.agent = mock
            app._agent_provider = app.provider
            app.query_one("#input", appmod.InputArea).text = "/compact"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if mock.run_force_compact_calls > 0:
                    break
            assert mock.run_force_compact_calls == 1
            texts = _history_texts(app)
            assert any("已压缩，token 从 500 降至 300" in t for t in texts)

    _run(run())


def test_tui_slash_compact_failure_shows_error(monkeypatch):
    """/compact 失败时显示压缩失败系统消息，不退出。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()

            class FailAgent(MockAgent):
                async def run_force_compact(self, conv, tool_defs):  # type: ignore[no-untyped-def]
                    self.run_force_compact_calls += 1
                    raise RuntimeError("boom")

            mock = FailAgent()
            app.agent = mock
            app._agent_provider = app.provider
            app.query_one("#input", appmod.InputArea).text = "/compact"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if mock.run_force_compact_calls > 0:
                    break
            await pilot.pause()
            texts = _history_texts(app)
            assert any("压缩失败" in t for t in texts)
            assert app._running  # 未退出

    _run(run())


def test_tui_unknown_slash_command_friendly(monkeypatch):
    """/unknown 给出友好提示，不发 LLM。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            mock = MockAgent()
            app.agent = mock
            app._agent_provider = app.provider
            app.query_one("#input", appmod.InputArea).text = "/unknown"
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
            assert mock.run_calls == 0
            texts = _history_texts(app)
            assert any("未知命令" in t and "/compact" in t for t in texts)

    _run(run())


def test_tui_migrated_plan_still_works(monkeypatch):
    """/plan 切到 PLAN 模式，输出提示，不调 run。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            mock = MockAgent()
            app.agent = mock
            app._agent_provider = app.provider
            app.query_one("#input", appmod.InputArea).text = "/plan"
            await pilot.press("enter")
            await pilot.pause()
            assert app.mode == Mode.PLAN
            assert mock.run_calls == 0
            texts = _history_texts(app)
            assert any("计划模式" in t for t in texts)

    _run(run())


def test_tui_migrated_do_still_works(monkeypatch):
    """/do 切回 DEFAULT、conv 追加 EXECUTE_DIRECTIVE 并启动一轮 run。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            # 先进 PLAN，再 /do 切回
            app.mode = Mode.PLAN
            app.query_one("#input", appmod.InputArea).text = "/do"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if app.state == SessionState.IDLE:
                    break
            assert app.mode == Mode.DEFAULT
            assert any(m.content == EXECUTE_DIRECTIVE for m in app.conv.messages())

    _run(run())


def test_tui_migrated_exit_still_works(monkeypatch):
    """/exit 走命令路径调 _quit，行为与迁移前一致。"""
    fake = FakeProvider(events=[StreamEvent(done=True)])
    monkeypatch.setattr(appmod, "new_provider", lambda cfg: fake)
    quit_calls = {"n": 0}

    async def run():
        app = KoyoCodeApp([_provider_cfg()], engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()

            original_quit = app._quit

            def fake_quit():
                quit_calls["n"] += 1

            app._quit = fake_quit  # type: ignore[method-assign]
            app.query_one("#input", appmod.InputArea).text = "/exit"
            await pilot.press("enter")
            await pilot.pause()
            assert quit_calls["n"] == 1
            _ = original_quit

    _run(run())
