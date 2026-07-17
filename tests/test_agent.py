"""Agent ReAct 循环单测（AC1/AC3/AC4/AC8/AC9/AC13）。

用实现 ``Provider`` Protocol 的 ``FakeProvider`` 按脚本序列逐轮吐出 ``StreamEvent``：
- 场景 A：多轮链路（读文件 → 续答），断言事件序列与历史。
- 场景 B：迭代上限——模型恒返工具调用，断言恰好 ``MAX_ITERATIONS`` 轮后停。
- 场景 C：连续未知工具——断言 ``MAX_UNKNOWN_RUN`` 轮后停；混入已知工具则计数重置。
- 场景 D：保序分批并发——插桩工具断言两只读并发、有副作用工具在其后开始、结果顺序保序。
- 场景 E：取消历史一致——执行期间取消，断言历史配对合法且可继续对话。
- 场景 F：Plan Mode 工具集——断言只收到只读工具定义与计划态系统后缀。
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from koyocode.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    Event,
    Mode,
    Phase,
)
from koyocode.conversation import Conversation
from koyocode.llm import StreamEvent, ToolCall
from koyocode.llm import Usage as LLMUsage
from koyocode.prompt import PLAN_MODE_REMINDER
from koyocode.tool import Registry, Result, new_default_registry

_READ_TARGET = Path(__file__).resolve().parent.parent / "pyproject.toml"


class FakeProvider:
    """按预设脚本序列依次吐出 ``StreamEvent``，实现 ``Provider`` Protocol。

    脚本耗尽后重放最后一个脚本（供「恒返工具调用」类用例复用同一脚本）。
    记录每次调用收到的 ``tools`` / ``system_suffix`` 供断言（场景 F）。
    """

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._i = 0
        self.calls = 0
        self.received_tools: list[list] = []
        self.received_suffix: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, msgs, tools, system_suffix=""):  # noqa: ANN001 — 实现 Protocol
        self.calls += 1
        self.received_tools.append(tools)
        self.received_suffix.append(system_suffix)
        script = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        for ev in script:
            await asyncio.sleep(0)
            yield ev


def _read_call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="read_file", input=json.dumps({"path": str(_READ_TARGET)}))


async def _collect(
    agent: Agent, conv: Conversation, mode: Mode, cancel: asyncio.Event
) -> list[Event]:
    events: list[Event] = []
    async for ev in agent.run(conv, mode, cancel):
        events.append(ev)
    return events


# ───────── 场景 A：多轮自动连环（AC1）─────────


@pytest.mark.asyncio
async def test_multi_turn_chain_ac1() -> None:
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(text="我先读取该文件"),
                StreamEvent(tool_calls=[_read_call("c1")]),
                StreamEvent(usage=LLMUsage(input_tokens=10, output_tokens=5)),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="已读取并给出总结"),
                StreamEvent(usage=LLMUsage(input_tokens=8, output_tokens=4)),
                StreamEvent(done=True),
            ],
        ]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("读 pyproject.toml 并总结")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    assert [e.iter for e in events if e.iter] == [1, 2]

    tool_events = [e for e in events if e.tool is not None]
    assert len(tool_events) == 2
    assert tool_events[0].tool.name == "read_file"
    assert tool_events[0].tool.phase == Phase.START
    assert tool_events[1].tool.phase == Phase.END
    assert tool_events[1].tool.is_error is False

    usages = [e.usage for e in events if e.usage is not None]
    assert [(u.input, u.output) for u in usages] == [(10, 5), (8, 4)]

    assert any(e.text == "我先读取该文件" for e in events)
    assert any(e.text == "已读取并给出总结" for e in events)
    assert events[-1].done is True

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].content == "我先读取该文件"
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].role == "tool"
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[3].content == "已读取并给出总结"


@pytest.mark.asyncio
async def test_no_tool_calls_direct_done() -> None:
    """自然完成（AC2）：无工具调用的纯文本直接结束循环。"""
    provider = FakeProvider(
        scripts=[[StreamEvent(text="你好"), StreamEvent(text="，世界"), StreamEvent(done=True)]]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("打招呼")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    assert [e.text for e in events if e.text] == ["你好", "，世界"]
    assert events[-1].done is True
    assert provider.calls == 1
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "你好，世界"


# ───────── 场景 B：迭代上限兜底（AC3）─────────


class EchoTool:
    """最小只读桩工具：立即返回固定结果。"""

    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "echo"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        return Result(content="ok")


@pytest.mark.asyncio
async def test_iteration_cap_stops_at_max_ac3() -> None:
    call = ToolCall(id="c1", name="echo", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    reg = Registry()
    reg.register(EchoTool())
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("keep going")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    assert provider.calls == MAX_ITERATIONS
    assert any(e.notice == NOTICE_MAX_ITER for e in events)
    assert events[-1].done is True
    assert conv.last_role() == "assistant"


# ───────── 场景 C：连续未知工具停止（AC4）─────────


@pytest.mark.asyncio
async def test_consecutive_unknown_tools_stops_ac4() -> None:
    call = ToolCall(id="c", name="ghost", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    reg = Registry()  # 空注册中心：ghost 永远未知
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    assert provider.calls == MAX_UNKNOWN_RUN
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert events[-1].done is True
    assert conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_unknown_run_resets_when_known_tool_appears() -> None:
    def ghost(cid: str) -> ToolCall:
        return ToolCall(id=cid, name="ghost", input="{}")

    def echo(cid: str) -> ToolCall:
        return ToolCall(id=cid, name="echo", input="{}")

    scripts = [
        [StreamEvent(tool_calls=[ghost("c1")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[ghost("c2")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[echo("c3")]), StreamEvent(done=True)],  # 已知工具，计数重置
        [StreamEvent(tool_calls=[ghost("c4")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[ghost("c5")]), StreamEvent(done=True)],
        [StreamEvent(text="完成"), StreamEvent(done=True)],
    ]
    provider = FakeProvider(scripts=scripts)
    reg = Registry()
    reg.register(EchoTool())
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    assert provider.calls == 6
    assert not any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert events[-1].done is True


# ───────── 场景 D：保序分批并发（AC8/N6）─────────


class _ConcurrencyTracker:
    """记录只读/有副作用桩工具的开始结束时刻与并发峰值。"""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self.events: list[tuple[str, str, float]] = []

    def start(self, label: str) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)
        self.events.append((label, "start", time.monotonic()))

    def end(self, label: str) -> None:
        self.current -= 1
        self.events.append((label, "end", time.monotonic()))


class _ROStubTool:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self._tracker = tracker

    def name(self) -> str:
        return "stub_ro"

    def description(self) -> str:
        return "stub read-only"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["label"]
        self._tracker.start(label)
        await asyncio.sleep(0.05)
        self._tracker.end(label)
        return Result(content=f"{label}-done")


class _RWStubTool:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self._tracker = tracker

    def name(self) -> str:
        return "stub_rw"

    def description(self) -> str:
        return "stub side-effect"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["label"]
        self._tracker.start(label)
        await asyncio.sleep(0.01)
        self._tracker.end(label)
        return Result(content=f"{label}-done")


@pytest.mark.asyncio
async def test_ordered_batched_concurrency_ac8() -> None:
    tracker = _ConcurrencyTracker()
    reg = Registry()
    reg.register(_ROStubTool(tracker))
    reg.register(_RWStubTool(tracker))

    ro1 = ToolCall(id="ro1", name="stub_ro", input=json.dumps({"label": "ro1"}))
    ro2 = ToolCall(id="ro2", name="stub_ro", input=json.dumps({"label": "ro2"}))
    rw1 = ToolCall(id="rw1", name="stub_rw", input=json.dumps({"label": "rw1"}))

    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[ro1, ro2, rw1]), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.NORMAL, asyncio.Event())

    # 两只读并发峰值 >= 2
    assert tracker.peak >= 2

    ro_ends = [
        t for label, phase, t in tracker.events if phase == "end" and label in ("ro1", "ro2")
    ]
    rw_starts = [t for label, phase, t in tracker.events if phase == "start" and label == "rw1"]
    assert rw_starts[0] > max(ro_ends)

    # 结果按原始调用序回灌
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert [r.tool_call_id for r in tool_msg.tool_results] == ["ro1", "ro2", "rw1"]
    assert events[-1].done is True


# ───────── 场景 E：取消历史一致（AC9）─────────


class _SleepyTool:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    def name(self) -> str:
        return "sleepy"

    def description(self) -> str:
        return "stub"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(self._delay)
        return Result(content="finished")


@pytest.mark.asyncio
async def test_cancellation_keeps_history_consistent_ac9() -> None:
    reg = Registry()
    reg.register(_SleepyTool(delay=0.2))
    call = ToolCall(id="c1", name="sleepy", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("go slow")
    cancel = asyncio.Event()

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_soon())
    await _collect(agent, conv, Mode.NORMAL, cancel)
    await cancel_task

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[3].content == NOTICE_CANCELLED
    assert conv.last_role() == "assistant"

    # 取消后历史未坏，可继续对话
    provider2 = FakeProvider(scripts=[[StreamEvent(text="继续没问题"), StreamEvent(done=True)]])
    agent2 = Agent(provider2, reg)
    conv.add_user("继续")
    events2 = await _collect(agent2, conv, Mode.NORMAL, asyncio.Event())

    assert events2[-1].done is True
    assert conv.messages()[-1].content == "继续没问题"


# ───────── 场景 F：Plan Mode 工具集（AC13）─────────


@pytest.mark.asyncio
async def test_plan_mode_uses_read_only_tools_and_suffix_ac13() -> None:
    provider = FakeProvider(scripts=[[StreamEvent(text="这是计划"), StreamEvent(done=True)]])
    reg = new_default_registry()
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("给登录功能加单测的方案")

    events = await _collect(agent, conv, Mode.PLAN, asyncio.Event())

    assert provider.received_suffix[0] == PLAN_MODE_REMINDER
    assert [t.name for t in provider.received_tools[0]] == ["read_file", "glob", "grep"]
    assert events[-1].done is True
