"""token.py 估算单测。"""

from __future__ import annotations

import math

from koyocode.compact.token import estimate_tokens, message_chars, usage_anchor
from koyocode.llm import Message, Usage


def test_estimate_tokens_zero():
    assert estimate_tokens(0, [], 0) == 0


def test_estimate_tokens_increment():
    m = Message(role="user", content="a" * 350)
    assert estimate_tokens(5000, [m], 0) == 5000 + math.ceil(350 / 3.5)


def test_estimate_tokens_anchor_msg_len():
    m1 = Message(role="user", content="a" * 350)
    m2 = Message(role="user", content="b" * 350)
    # anchor_msg_len=1：只算 m2 的增量
    assert estimate_tokens(1000, [m1, m2], 1) == 1000 + math.ceil(350 / 3.5)


def test_estimate_tokens_large_no_issue():
    m = Message(role="user", content="x" * 10_000_000)
    assert estimate_tokens(2_000_000_000, [m], 0) > 2_000_000_000


def test_usage_anchor_sum():
    u = Usage(input_tokens=10, output_tokens=20, cache_read=5, cache_write=5)
    assert usage_anchor(u) == 40
    assert isinstance(usage_anchor(u), int)


def test_message_chars_counts_tool_fields():
    from koyocode.llm import ToolResultBlock, ToolUseBlock

    m = Message(
        role="assistant",
        content="hello",
        tool_uses=[ToolUseBlock(id="t", name="x", input='{"a":1}')],
    )
    t = Message(role="tool", tool_results=[ToolResultBlock(tool_call_id="t", content="result")])
    chars = message_chars([m, t])
    assert chars == len(b"hello") + len(b'{"a":1}') + len(b"result")
