"""conversation 模块单测。"""

from koyocode.conversation import Conversation
from koyocode.llm import Message, ToolResultBlock, ToolUseBlock


def test_empty() -> None:
    c = Conversation()
    assert c.messages() == []


def test_last_role() -> None:
    c = Conversation()
    assert c.last_role() == ""
    c.add_user("hi")
    assert c.last_role() == "user"
    c.add_assistant_with_tool_uses("", [ToolUseBlock(id="c1", name="read_file", input="{}")])
    c.add_tool_results([ToolResultBlock(tool_call_id="c1", content="ok")])
    assert c.last_role() == "tool"
    c.add_assistant("done")
    assert c.last_role() == "assistant"


def test_add_and_order() -> None:
    c = Conversation()
    c.add_user("hi")
    c.add_assistant("hello")
    c.add_user("again")
    msgs = c.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.content for m in msgs] == ["hi", "hello", "again"]


def test_messages_returns_copy() -> None:
    c = Conversation()
    c.add_user("a")
    snapshot = c.messages()
    snapshot.append(Message(role="user", content="b"))
    assert len(c.messages()) == 1


def test_tool_turns_roundtrip() -> None:
    """assistant 工具调用回合 + tool 结果回合按序入历史且内容正确（F6）。"""
    c = Conversation()
    c.add_user("读文件")
    calls = [ToolUseBlock(id="call_1", name="read_file", input='{"path":"a.txt"}')]
    c.add_assistant_with_tool_uses("我先读文件", calls)
    results = [ToolResultBlock(tool_call_id="call_1", content="文件内容", is_error=False)]
    c.add_tool_results(results)
    c.add_assistant("这是文件的内容总结")

    msgs = c.messages()
    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    # assistant 工具调用回合携带 tool_uses，正文为 preamble
    assert msgs[1].content == "我先读文件"
    assert msgs[1].tool_uses == calls
    assert msgs[1].tool_uses[0].name == "read_file"
    # tool 结果回合携带 tool_results
    assert msgs[2].role == "tool"
    assert msgs[2].tool_results == results
    assert msgs[2].tool_results[0].tool_call_id == "call_1"
    # 末尾为最终文本答复
    assert msgs[3].content == "这是文件的内容总结"
    assert msgs[3].tool_uses == []
