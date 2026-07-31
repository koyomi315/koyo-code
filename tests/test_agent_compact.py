"""Agent 上下文压缩集成单测（ch08）：紧急压缩重试 + Compact 状态事件。

用 ``FakeProvider(scripts)`` 按调用序消费 ``StreamEvent``，驱动真实 ``Agent``。
通过 ``SessionRuntime.usage_anchor`` 与 ``context_window`` 控制自动阈值触发，
避免依赖真实 provider。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from koyocode.agent import Agent, Event, Mode
from koyocode.agent.event import CompactPhase
from koyocode.agent.runtime import new_session_runtime
from koyocode.conversation import Conversation
from koyocode.llm import PromptTooLongError, StreamEvent, ToolUseBlock
from koyocode.llm import Usage as LLMUsage
from koyocode.permission import new_engine
from koyocode.tool import new_default_registry

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class FakeProvider:
    """按调用序消费 scripts，耗尽后重放最后一个；记录每次请求。"""

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._i = 0
        self.calls = 0
        self.received_reqs: list = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):  # noqa: ANN001
        self.calls += 1
        self.received_reqs.append(req)
        script = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        for ev in script:
            await asyncio.sleep(0)
            yield ev


def _engine():
    e, _ = new_engine(_REPO_ROOT)
    return e


def _ptl() -> StreamEvent:
    return StreamEvent(err=PromptTooLongError("prompt too long"))


async def _collect(agent, conv, mode=Mode.DEFAULT, cancel=None):  # noqa: ANN001
    cancel = cancel or asyncio.Event()
    events: list[Event] = []
    async for ev in agent.run(conv, mode, cancel):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_agent_emergency_compact_succeeds() -> None:
    """PTL -> 紧急压缩 -> 重试一次成功。"""
    provider = FakeProvider(
        scripts=[
            [_ptl()],  # 第 1 次主对话撞墙
            [StreamEvent(text="<summary>摘要</summary>", done=True)],  # 紧急摘要请求
            [StreamEvent(text="恢复答复"), StreamEvent(done=True)],  # 重试主对话
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    events = await _collect(agent, conv)

    assert events[-1].done is True
    assert conv.last_role() == "assistant"
    assert "恢复答复" in conv.messages()[-1].content
    assert provider.calls == 3  # 主对话 + 摘要 + 重试


@pytest.mark.asyncio
async def test_agent_emergency_compact_re_raise_on_second_ptl() -> None:
    """重试再次 PTL -> 上抛，不进入第三次紧急压缩。"""
    provider = FakeProvider(
        scripts=[
            [_ptl()],  # 主对话撞墙
            [StreamEvent(text="<summary>摘要</summary>", done=True)],  # 紧急摘要
            [_ptl()],  # 重试再次撞墙
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    events = await _collect(agent, conv)

    err_events = [e for e in events if e.err is not None]
    assert len(err_events) >= 1
    assert isinstance(err_events[-1].err, PromptTooLongError)
    assert provider.calls == 3  # 不再有第 4 次


@pytest.mark.asyncio
async def test_agent_emergency_compact_unrecoverable_when_still_too_big() -> None:
    """紧急压缩后重估仍超 context_window - MANUAL_SAFETY_MARGIN -> 不重试，上抛。"""
    # context_window=33000 触发 sanity check 跳过 auto layer2；conv 大到摘要后仍超 30000
    provider = FakeProvider(
        scripts=[
            [_ptl()],  # 主对话撞墙
            [StreamEvent(text="<summary>摘要</summary>", done=True)],  # 紧急摘要
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=33000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("x" * 110000)  # /3.5 ≈ 31428，摘要后 recent 仍保留 -> after > 30000
    events = await _collect(agent, conv)

    err_events = [e for e in events if e.err is not None]
    assert len(err_events) >= 1
    assert isinstance(err_events[-1].err, PromptTooLongError)
    assert provider.calls == 2  # 主对话 + 摘要，无重试主对话


@pytest.mark.asyncio
async def test_agent_emits_emergency_compact_events() -> None:
    """紧急压缩触发时 emit BEFORE_EMERGENCY + AFTER_EMERGENCY 一对事件。"""
    provider = FakeProvider(
        scripts=[
            [_ptl()],
            [StreamEvent(text="<summary>摘要</summary>", done=True)],
            [StreamEvent(text="恢复"), StreamEvent(done=True)],
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    events = await _collect(agent, conv)

    compact_phases = [e.compact.phase for e in events if e.compact is not None]
    assert CompactPhase.BEFORE_EMERGENCY in compact_phases
    assert CompactPhase.AFTER_EMERGENCY in compact_phases
    # BEFORE 在 AFTER 之前
    assert compact_phases.index(CompactPhase.BEFORE_EMERGENCY) < compact_phases.index(
        CompactPhase.AFTER_EMERGENCY
    )


@pytest.mark.asyncio
async def test_agent_emits_auto_compact_events() -> None:
    """估算 token 超阈值时 emit BEFORE_AUTO + AFTER_AUTO，before > after。"""
    provider = FakeProvider(
        scripts=[
            [StreamEvent(text="<summary>摘要</summary>", done=True)],  # 自动摘要请求
            [StreamEvent(text="答复"), StreamEvent(done=True)],  # 主对话
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    runtime.usage_anchor = 200000  # 让首轮 est 超过 167000 阈值
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")
    events = await _collect(agent, conv)

    compact_phases = [e.compact.phase for e in events if e.compact is not None]
    assert compact_phases.count(CompactPhase.BEFORE_AUTO) == 1
    assert compact_phases.count(CompactPhase.AFTER_AUTO) == 1
    after_ev = next(e for e in events if e.compact and e.compact.phase == CompactPhase.AFTER_AUTO)
    assert after_ev.compact.err is None
    assert after_ev.compact.before > after_ev.compact.after
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_agent_no_compact_event_below_threshold() -> None:
    """估算 token 远低于阈值时不发任何 Compact 事件。"""
    provider = FakeProvider(scripts=[[StreamEvent(text="答复"), StreamEvent(done=True)]])
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    events = await _collect(agent, conv)

    assert not any(e.compact is not None for e in events)
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_agent_usage_anchor_replaced_not_accumulated() -> None:
    """主对话路径 usage 锚点被替换（非累加）；摘要请求不更新锚点。"""
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(text="答复"),
                StreamEvent(usage=LLMUsage(input_tokens=100, output_tokens=50)),
                StreamEvent(done=True),
            ]
        ]
    )
    runtime = new_session_runtime(_REPO_ROOT, context_window=200000)
    agent = Agent(provider, new_default_registry(), "test", _engine(), runtime=runtime)
    conv = Conversation()
    conv.add_user("hello")
    await _collect(agent, conv)

    # usage_anchor = 100 + 50 + 0 + 0 = 150（替换，非累加）
    assert runtime.usage_anchor == 150
    # 锚点记录时 conv 尚未追加本轮 assistant，长度为 1
    assert runtime.anchor_msg_len == 1


@pytest.mark.asyncio
async def test_agent_readfile_tracked_to_recovery(tmp_path: Path) -> None:
    """ReadFile 成功后用纯净字节（不带行号）记录到 recovery。"""
    target = tmp_path / "data.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(
                    tool_uses=[
                        ToolUseBlock(
                            id="c1",
                            name="read_file",
                            input=json.dumps({"path": str(target)}),
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    runtime = new_session_runtime(str(tmp_path), context_window=200000)
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, new_default_registry(), "test", engine, runtime=runtime)
    conv = Conversation()
    conv.add_user("读文件")
    await _collect(agent, conv)

    snap = runtime.recovery.snapshot()
    assert any(str(target.resolve()) == r.path for r in snap)
    rec = next(r for r in snap if r.path == str(target.resolve()))
    # 纯净字节，不含行号前缀
    assert rec.content == "line1\nline2\n"
