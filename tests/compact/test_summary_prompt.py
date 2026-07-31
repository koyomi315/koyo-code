"""summary_prompt.py 模板与解析单测。"""

from __future__ import annotations

from koyocode.compact.summary_prompt import (
    SUMMARY_SECTIONS,
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from koyocode.llm import Message, ToolResultBlock, ToolUseBlock


def test_build_summary_prompt_shape():
    p = build_summary_prompt([Message(role="user", content="hi")])
    assert len(p) == 1
    assert p[0].role == "user"
    c = p[0].content
    assert "<analysis>" in c and "<summary>" in c
    for s in SUMMARY_SECTIONS:
        assert s in c
    assert "不要调用任何工具" in c
    assert "[conversation]" in c


def test_serialize_conversation_deterministic():
    msgs = [
        Message(role="user", content="u"),
        Message(
            role="assistant",
            content="a",
            tool_uses=[ToolUseBlock(id="t", name="read_file", input='{"path":"x"}')],
        ),
        Message(role="tool", tool_results=[ToolResultBlock(tool_call_id="t", content="r")]),
    ]
    s1 = serialize_conversation(msgs)
    s2 = serialize_conversation(msgs)
    assert s1 == s2
    assert "user: u" in s1
    assert "[call read_file id=t args=" in s1
    assert "[result id=t is_error=False] r" in s1


def test_extract_summary_standard():
    assert extract_summary("abc<summary>xx</summary>yy") == "xx"


def test_extract_summary_missing():
    assert extract_summary("no tags here") == "no tags here"


def test_extract_summary_last_pair():
    assert extract_summary("a<summary>1</summary>b<summary>2</summary>c") == "2"
