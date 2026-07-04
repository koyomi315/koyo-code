"""LLM 协议层：协议无关的 ``Provider`` Protocol、统一消息/事件类型与工厂。

anthropic / openai 两个适配器各自封装官方 SDK，统一吐出文本增量
（思考增量在适配器内部丢弃），对上层暴露与协议无关的接口。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from koyocode.config import ProviderConfig


@dataclass
class Message:
    """单条对话消息。"""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class StreamEvent:
    """流式事件：text 为正文增量，done 表示本轮正常结束，err 与 done 互斥。"""

    text: str = ""
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    """协议无关的对话 provider。"""

    @property
    def name(self) -> str:
        """状态栏左侧显示的名称。"""
        ...

    @property
    def model(self) -> str:
        """状态栏右侧显示的模型名。"""
        ...

    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；内部注入 system prompt 与 thinking 配置。

        思考增量内部丢弃；以 async generator 吐出 ``StreamEvent``。
        调用方 cancel 对应 task 时，``async for`` 抛 ``CancelledError``，
        SDK 流由 ``async with`` 上下文自动清理。
        """
        ...


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 ``cfg.protocol`` 构造对应适配器。未知协议抛 ``ValueError``。"""
    if cfg.protocol == "anthropic":
        from koyocode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from koyocode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError(f"未知协议: {cfg.protocol}")
