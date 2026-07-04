"""单会话多轮历史（进程内维护，不持久化）。

历史按 user / assistant 交替追加；每一轮新请求携带此前全部上下文。
"""

from koyocode.llm import Message


class Conversation:
    """进程内单会话多轮历史。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        """追加一条助手消息。"""
        self._messages.append(Message(role="assistant", content=text))

    def messages(self) -> list[Message]:
        """返回历史的副本（修改不影响内部状态）。"""
        return list(self._messages)
