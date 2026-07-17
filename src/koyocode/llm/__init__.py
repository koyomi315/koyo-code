"""LLM 协议层：协议无关的 ``Provider`` Protocol、统一消息/事件类型与工厂。

anthropic / openai 两个适配器各自封装官方 SDK，统一吐出文本增量
（思考增量在适配器内部丢弃），对上层暴露与协议无关的接口。

本章扩展：新增 ``ToolCall`` / ``ToolResult`` / ``ToolDefinition`` / ``ROLE_TOOL``
等协议无关类型；``Message`` 可承载 assistant 工具调用回合与 ``ROLE_TOOL`` 结果回合；
``StreamEvent`` 增 ``tool_calls`` 字段以在 turn 结束时上抛模型请求的工具调用。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from koyocode.config import ProviderConfig

# 消息角色常量：ROLE_TOOL 携带工具执行结果回合。
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。

    ``id`` 为 provider 侧调用 id，回灌结果时配对；``input`` 为拼接完成的
    JSON 参数字符串（raw JSON），由适配器在回灌时按需 ``json.loads``。
    """

    id: str
    name: str
    input: str


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    """协议无关地承载一轮请求的 token 用量。"""

    input_tokens: int = 0
    """本轮请求输入（含完整历史）token 数。"""
    output_tokens: int = 0
    """本轮响应输出 token 数。"""


@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。

    ``input_schema`` 为完整 JSON Schema（type/properties/required），
    OpenAI 直接用整对象；Anthropic 取其作为 ``input_schema``。
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Message:
    """单条对话消息。

    ``tool_calls`` 仅 assistant 回合使用（模型请求执行的工具调用）；
    ``tool_results`` 仅 ``ROLE_TOOL`` 回合使用（对应执行结果）。
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class StreamEvent:
    """流式事件。

    四态语义：``text`` 为正文增量；``tool_calls`` 非空表示本轮模型请求执行这些
    工具（在 ``done`` 之前发出）；``done`` 表示本轮正常结束；``err`` 与 ``done`` 互斥。
    ``usage`` 非空：本轮 token 用量，在 ``done`` 之前一次性发出。
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
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

    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；内部注入 system prompt 与 thinking 配置。

        ``tools`` 为工具定义列表（空表示本次不带工具）；``system_suffix`` 非空时
        拼接到内置 ``SYSTEM_PROMPT`` 之后（Plan Mode 计划态约束），为空即普通模式；
        思考增量内部丢弃；以 async generator 吐出 ``StreamEvent``（含可能的
        ``tool_calls`` 与本轮结束前一次性上抛的 ``usage``）。调用方 cancel 对应
        task 时，``async for`` 抛 ``CancelledError``，SDK 流由 ``async with``
        上下文自动清理。
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
