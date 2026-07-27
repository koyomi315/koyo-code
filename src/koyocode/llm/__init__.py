"""LLM 协议层：协议无关的 ``Provider`` Protocol、统一消息/事件类型与工厂。

anthropic / openai 两个适配器各自封装官方 SDK，统一吐出文本增量
（思考增量在适配器内部丢弃），对上层暴露与协议无关的接口。

ch05 扩展（系统提示工程化）：``Provider.stream`` 入参改为 ``Request`` dataclass，
承载 ``messages / tools / system{stable, environment} / reminder``；``System`` 区分
可缓存稳定块与不缓存环境块（F3）；``Usage`` 增缓存写/读字段（F4）。系统提示由 agent
传入，llm 不再 import prompt（打破潜在循环依赖）。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from koyocode.config import ProviderConfig

# 消息角色常量：ROLE_TOOL 携带工具执行结果回合。
# 标注为各自 Literal 以便传入 Message.role: Literal[...] 时不被推断为宽 str。
ROLE_USER: Literal["user"] = "user"
ROLE_ASSISTANT: Literal["assistant"] = "assistant"
ROLE_TOOL: Literal["tool"] = "tool"


@dataclass
class ToolUseBlock:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。

    ``id`` 为 provider 侧调用 id，回灌结果时配对；``input`` 为拼接完成的
    JSON 参数字符串（raw JSON），由适配器在回灌时按需 ``json.loads``。
    """

    id: str
    name: str
    input: str


@dataclass
class ToolResultBlock:
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
    cache_write: int = 0
    """缓存写入 token 数（Anthropic: ``cache_creation_input_tokens``；OpenAI: 恒 0）。"""
    cache_read: int = 0
    """缓存读取 token 数（Anthropic: ``cache_read_input_tokens``；OpenAI: ``cached_tokens``）。"""


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

    ``tool_uses`` 仅 assistant 回合使用（模型请求执行的工具调用）；
    ``tool_results`` 仅 ``ROLE_TOOL`` 回合使用（对应执行结果）。
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)


@dataclass
class System:
    """系统提示的稳定块与变化环境块（F3）。

    ``stable`` 为可缓存的稳定系统模块装配文本（跨轮逐字节一致，N1）；
    ``environment`` 为不缓存的环境信息段（随采集时刻变化）。provider 据此分别打
    缓存断点（stable）/ 不打（environment），二者物理上分属不同内容块。
    """

    stable: str = ""
    environment: str = ""


@dataclass
class Request:
    """一轮流式请求的全部入参（替换 stream 位置参数，F3/F6/F7）。

    ``messages`` 为持久对话历史（不含本轮 reminder）；``tools`` 为本轮工具集
    （普通=全量 / 规划=只读）；``system`` 承载稳定系统提示与环境段；``reminder`` 为
    本轮 system-reminder 内容（已含标签，空=不注入，每轮动态构造、不写入持久历史，N3）。
    """

    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    system: System = field(default_factory=System)
    reminder: str = ""


@dataclass
class StreamEvent:
    """流式事件。

    四态语义：``text`` 为正文增量；``tool_uses`` 非空表示本轮模型请求执行这些
    工具（在 ``done`` 之前发出）；``done`` 表示本轮正常结束；``err`` 与 ``done`` 互斥。
    ``usage`` 非空：本轮 token 用量（含缓存写/读），在 ``done`` 之前一次性发出。
    """

    text: str = ""
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
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

    def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；系统提示、工具、环境、reminder 均由 ``req`` 承载（F3/F6）。

        ``req.system.stable`` 走可缓存通道、``req.system.environment`` 走不缓存通道；
        ``req.reminder`` 非空时按各协议安全织入消息通道（不写入持久历史，N3）。以 async
        generator 吐出 ``StreamEvent``（含可能的 ``tool_uses`` 与本轮结束前一次性上抛的
        ``usage``，含缓存写/读字段）。调用方 cancel 对应 task 时，``async for`` 抛
        ``CancelledError``，SDK 流由 ``async with`` 上下文自动清理。
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
