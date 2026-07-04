"""conversation 模块单测。"""

from koyocode.conversation import Conversation
from koyocode.llm import Message


def test_empty() -> None:
    c = Conversation()
    assert c.messages() == []


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
