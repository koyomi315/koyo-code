"""layer2.py 摘要与重试单测。"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from koyocode.compact.layer2 import (
    _join_after_summary,
    group_by_user_turn,
    pick_recent_tail,
    ptl_retry,
    run_summary,
    summarize_once,
)
from koyocode.compact.state import CompactCircuitBreaker, RecoveryState
from koyocode.compact.token import message_chars
from koyocode.conversation import Conversation
from koyocode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    PromptTooLongError,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
)


def _make_in(provider, conv=None, tool_defs=None):
    if conv is None:
        conv = Conversation()
        conv.add_user("hello")
        conv.add_assistant("hi")
    return SimpleNamespace(
        conv=conv,
        provider=provider,
        recovery=RecoveryState(),
        tool_defs=tool_defs or [],
        estimated_token=1000,
        auto_tracking=CompactCircuitBreaker(),
    )


def test_group_by_user_turn():
    msgs = [
        Message(role=ROLE_USER, content="u1"),
        Message(role=ROLE_ASSISTANT, content="a1"),
        Message(role=ROLE_TOOL, tool_results=[ToolResultBlock(tool_call_id="t", content="r")]),
        Message(role=ROLE_USER, content="u2"),
        Message(role=ROLE_ASSISTANT, content="a2"),
    ]
    g = group_by_user_turn(msgs)
    assert len(g) == 2
    assert len(g[0]) == 3 and len(g[1]) == 2


def test_pick_recent_tail_boundary():
    msgs = []
    for _i in range(6):
        msgs.append(Message(role=ROLE_USER, content="U" * 40000))
        msgs.append(Message(role=ROLE_ASSISTANT, content="A" * 40000))
    recent = pick_recent_tail(msgs)
    assert len(recent) >= 5
    assert math.ceil(message_chars(recent) / 3.5) >= 10000


def test_pick_recent_tail_pair_fix():
    msgs = [
        Message(role=ROLE_USER, content="U" * 100000),
        Message(
            role=ROLE_ASSISTANT,
            content="A1",
            tool_uses=[ToolUseBlock(id="a", name="x", input="{}")],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResultBlock(tool_call_id="a", content="R" * 100000)],
        ),
        Message(
            role=ROLE_ASSISTANT,
            content="A2",
            tool_uses=[ToolUseBlock(id="b", name="x", input="{}")],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResultBlock(tool_call_id="b", content="R" * 100000)],
        ),
    ]
    recent = pick_recent_tail(msgs)
    assert recent[0].role != "tool"
    if recent[0].role == "assistant" and recent[0].tool_uses:
        assert any(m.role == "tool" for m in recent)


def test_join_after_summary_avoids_consecutive_user():
    sar = Message(role=ROLE_USER, content="summary")
    joined = _join_after_summary(sar, [Message(role=ROLE_USER, content="ru")])
    assert [m.role for m in joined] == ["user", "assistant", "user"]


async def test_run_summary_basic(make_fake_provider):
    provider = make_fake_provider()
    in_ = _make_in(provider)
    new_msgs = await run_summary(in_)
    assert new_msgs[0].role == "user"
    assert "## 历史会话摘要" in new_msgs[0].content
    assert provider.summarize_calls == 1


async def test_ptl_retry_drops_exactly_one_group_per_step(make_fake_provider):
    # 初始 PTL(6 组) -> 重试 PTL(5) -> PTL(4) -> 第 4 次(3 组)成功
    scripts = [
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(text="<summary>ok</summary>", done=True)],
    ]
    provider = make_fake_provider(scripts=scripts, default_summary=False)
    conv = Conversation()
    for i in range(6):
        conv.add_user(f"u{i}")
        conv.add_assistant(f"a{i}")
    in_ = _make_in(provider, conv=conv)
    with pytest.raises(PromptTooLongError):
        await summarize_once(in_, conv.messages())
    text = await ptl_retry(in_, conv.messages(), PromptTooLongError("first"))
    assert text == "ok"
    assert provider.stream_calls == 4  # 初始 + 3 次重试


async def test_ptl_retry_fall_to_percentage(make_fake_provider):
    # 前 4 次（初始 + 3 直接重试）都 PTL，第 5 次起按比例丢，最终成功
    scripts = [
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(err=PromptTooLongError("e"))],
        [StreamEvent(text="<summary>ok</summary>", done=True)],
    ]
    provider = make_fake_provider(scripts=scripts, default_summary=False)
    conv = Conversation()
    for i in range(8):
        conv.add_user(f"u{i}")
        conv.add_assistant(f"a{i}")
    in_ = _make_in(provider, conv=conv)
    with pytest.raises(PromptTooLongError):
        await summarize_once(in_, conv.messages())
    text = await ptl_retry(in_, conv.messages(), PromptTooLongError("first"))
    assert text == "ok"
    assert provider.stream_calls == 5  # 超过 3 次后按比例丢一次后成功


async def test_ptl_retry_stops_before_empty_messages(make_fake_provider):
    # 持续 PTL 直到 groups 耗尽，抛异常，不发送空请求
    scripts = [[StreamEvent(err=PromptTooLongError("e"))] for _ in range(20)]
    provider = make_fake_provider(scripts=scripts, default_summary=False)
    conv = Conversation()
    for i in range(3):
        conv.add_user(f"u{i}")
        conv.add_assistant(f"a{i}")
    in_ = _make_in(provider, conv=conv)
    with pytest.raises(PromptTooLongError):
        await summarize_once(in_, conv.messages())
    with pytest.raises(PromptTooLongError):
        await ptl_retry(in_, conv.messages(), PromptTooLongError("first"))
    # 每次请求的 messages 非空（不发送空请求）
    for req in provider.requests:
        assert len(req.messages) > 0
