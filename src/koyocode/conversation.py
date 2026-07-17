"""单会话多轮历史（进程内维护，不持久化）。

历史按 user / assistant 交替追加；每一轮新请求携带此前全部上下文。
本章扩展：支持 assistant 工具调用回合与 ``ROLE_TOOL`` 结果回合的追加（F6）。
"""

from koyocode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, ToolCall, ToolResult


class Conversation:
    """进程内单会话多轮历史。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        """追加一条助手消息。"""
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        """追加 assistant 工具调用回合：正文 preamble + 模型请求执行的工具调用。"""
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """追加 ``ROLE_TOOL`` 结果回合：对应各工具调用的执行结果。"""
        self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        """返回历史的副本（修改不影响内部状态）。"""
        return list(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的 role；空历史返回 ``""``。"""
        return self._messages[-1].role if self._messages else ""
