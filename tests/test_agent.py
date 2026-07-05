"""Agent 单轮闭环单测（AC8 链 + AC9 单轮上限）。

用实现 ``Provider`` Protocol 的 ``FakeProvider`` 编排两套脚本：
- (a) 请求#1 发起一次工具调用、请求#2 给出最终文本 → 断言事件链与 ``Conversation`` 历史；
- (b) 请求#1 发起工具调用、请求#2 仍发起工具调用 → 断言只执行一次、续答用单轮上限提示。
"""

import asyncio
import json
from pathlib import Path

import pytest

from koyocode.agent import Agent, Event, Phase
from koyocode.conversation import Conversation
from koyocode.llm import StreamEvent, ToolCall
from koyocode.tool import new_default_registry

_READ_TARGET = Path(__file__).resolve().parent.parent / "pyproject.toml"


class FakeProvider:
    """按预设脚本序列依次吐出 ``StreamEvent``，实现 ``Provider`` Protocol。"""

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._i = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, msgs, tools):  # noqa: ANN001 — 实现 Protocol
        script = self._scripts[self._i]
        self._i += 1
        for ev in script:
            await asyncio.sleep(0)
            yield ev


class CountingRegistry:
    """包裹真实注册中心，记录 ``execute`` 调用次数以验证 AC9。"""

    def __init__(self, real) -> None:  # noqa: ANN001
        self._real = real
        self.execute_calls: list[tuple[str, str]] = []

    def definitions(self):
        return self._real.definitions()

    def get(self, name):
        return self._real.get(name)

    def register(self, tool):
        return self._real.register(tool)

    async def execute(self, name, args, timeout=30.0):
        self.execute_calls.append((name, args))
        return await self._real.execute(name, args, timeout)


async def _collect(agent: Agent, conv: Conversation) -> list[Event]:
    events: list[Event] = []
    async for ev in agent.run(conv):
        events.append(ev)
    return events


def _read_call(call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="read_file",
        input=json.dumps({"path": str(_READ_TARGET)}),
    )


@pytest.mark.asyncio
async def test_tool_chain_single_turn() -> None:
    """AC8：请求#1 工具调用 → 执行 → 回灌 → 请求#2 最终文本，事件链与历史正确。"""
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(text="我先读取该文件"),
                StreamEvent(tool_calls=[_read_call("c1")]),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="已读取并给出总结"),
                StreamEvent(done=True),
            ],
        ]
    )
    reg = CountingRegistry(new_default_registry())
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("读 pyproject.toml 并总结")

    events = await _collect(agent, conv)

    # 事件链：preamble 文本 → 工具 START → 工具 END → 续答文本 → done
    tool_events = [e for e in events if e.tool is not None]
    assert len(tool_events) == 2
    assert tool_events[0].tool.name == "read_file"
    assert tool_events[0].tool.phase == Phase.START
    assert tool_events[1].tool.phase == Phase.END
    assert tool_events[1].tool.is_error is False
    assert tool_events[1].tool.result  # 结果非空
    assert any(e.text == "我先读取该文件" for e in events)
    assert any(e.text == "已读取并给出总结" for e in events)
    assert events[-1].done is True

    # 历史：[user, assistant(tool_calls), tool, assistant(最终文本)]
    msgs = conv.messages()
    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].content == "我先读取该文件"
    assert len(msgs[1].tool_calls) == 1
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].role == "tool"
    assert len(msgs[2].tool_results) == 1
    assert msgs[2].tool_results[0].is_error is False
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[3].content == "已读取并给出总结"
    assert msgs[3].tool_calls == []

    # 工具执行恰好一次
    assert len(reg.execute_calls) == 1
    assert reg.execute_calls[0][0] == "read_file"


@pytest.mark.asyncio
async def test_single_turn_limit_ignores_second_round_tools() -> None:
    """AC9：请求#2 仍发起工具调用时，忽略不执行，续答用单轮上限提示占位。"""
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(tool_calls=[_read_call("c1")]),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(tool_calls=[_read_call("c2")]),
                StreamEvent(done=True),
            ],
        ]
    )
    reg = CountingRegistry(new_default_registry())
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("读文件")

    events = await _collect(agent, conv)

    # 只对请求#1 的工具发起一次 START/END，请求#2 的工具调用被忽略
    tool_events = [e for e in events if e.tool is not None]
    assert len(tool_events) == 2
    assert all(t.tool.name == "read_file" for t in tool_events)

    # 续答为空 → 单轮上限提示作为最终文本（非空）
    assert events[-1].done is True
    final_text_events = [e.text for e in events if e.text]
    assert final_text_events  # 至少占位提示
    assert "单轮" in final_text_events[-1]

    # 历史：[user, assistant(tool_calls), tool, assistant(占位提示)]
    msgs = conv.messages()
    assert len(msgs) == 4
    assert msgs[3].role == "assistant"
    assert msgs[3].content  # 非空（占位提示）
    assert msgs[3].tool_calls == []

    # 工具执行恰好一次（c1），c2 未执行
    assert len(reg.execute_calls) == 1


@pytest.mark.asyncio
async def test_no_tool_calls_direct_done() -> None:
    """请求#1 无工具调用时直接以文本结束（无需请求#2）。"""
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(text="你好"),
                StreamEvent(text="，世界"),
                StreamEvent(done=True),
            ]
        ]
    )
    reg = CountingRegistry(new_default_registry())
    agent = Agent(provider, reg)

    conv = Conversation()
    conv.add_user("打招呼")

    events = await _collect(agent, conv)

    assert [e.text for e in events if e.text] == ["你好", "，世界"]
    assert events[-1].done is True
    # 无工具事件、无执行
    assert not [e for e in events if e.tool is not None]
    assert reg.execute_calls == []
    # 历史：[user, assistant(最终文本)]
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "你好，世界"
