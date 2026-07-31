"""单会话多轮历史（进程内维护，不持久化）。

历史按 user / assistant 交替追加；每一轮新请求携带此前全部上下文。
本章扩展：支持 assistant 工具调用回合与 ``ROLE_TOOL`` 结果回合的追加（F6）；
新增 ``replace_history`` 整体替换（ch08 上下文压缩摘要后重建历史）。所有读写
方法用 ``threading.RLock`` 保护，防止 ``replace_history`` 与 ``messages`` 并发
时拿到部分写入的列表。
"""

import copy
import threading

from koyocode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)


class Conversation:
    """进程内单会话多轮历史。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        with self._lock:
            self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        """追加一条助手消息。"""
        with self._lock:
            self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_uses(self, text: str, calls: list[ToolUseBlock]) -> None:
        """追加 assistant 工具调用回合：正文 preamble + 模型请求执行的工具调用。"""
        with self._lock:
            self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_uses=list(calls)))

    def add_tool_results(self, results: list[ToolResultBlock]) -> None:
        """追加 ``ROLE_TOOL`` 结果回合：对应各工具调用的执行结果。"""
        with self._lock:
            self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        """返回历史的副本（修改不影响内部状态）。"""
        with self._lock:
            return list(self._messages)

    def length(self) -> int:
        """返回当前历史消息条数。"""
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的 role；空历史返回 ``""``。"""
        with self._lock:
            return self._messages[-1].role if self._messages else ""

    def replace_history(self, msgs: list[Message]) -> None:
        """把内存列表整体替换为传入的 msgs（深拷贝，不暴露入参引用）。

        compact 摘要后用这个方法一次性丢弃旧历史并装入「摘要 + 恢复 + 近期原文」。
        传入 ``None`` / 空列表等价于清空历史。
        """
        with self._lock:
            self._messages = copy.deepcopy(msgs or [])
