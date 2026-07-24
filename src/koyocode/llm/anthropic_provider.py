"""anthropic 协议适配器：封装 ``AsyncAnthropic``，统一吐出 ``StreamEvent``。

- 系统提示分两块（F3）：``req.system.stable`` 带 ``cache_control: ephemeral`` 断点
  （可缓存，缓存前缀 = 全部工具 + 稳定块），``req.system.environment`` 不带（不缓存）。
- reminder 织入消息通道（F6/N3）：非空时并入末条 user 消息的 content 块，避免连续
  user 触发 400；末条非 user 时新起一条 user。
- 工具调用全流程：请求注入 ``tools``；流式仅取 ``text_delta``；流结束取
  ``get_final_message`` 收集 ``tool_use`` 组装 ``ToolCall`` 一次性上抛。
- 含工具历史的请求关闭 thinking（避免 Anthropic 要求回灌 thinking 签名导致 400）。
- 缓存用量解析（F4/N6）：``cache_creation_input_tokens`` / ``cache_read_input_tokens``，缺字段为 0。
- 异常转为 ``StreamEvent(err=...)``；``CancelledError`` 透传以支持 task 取消。
"""

import asyncio
import json
from collections.abc import AsyncIterator

import anthropic

from koyocode.config import ProviderConfig
from koyocode.llm import Message, Request, StreamEvent, ToolCall, ToolDefinition, Usage

_MAX_TOKENS = 4096
_THINKING_BUDGET = 2048


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
    """把协议无关 ``ToolDefinition`` 转为 anthropic tools 参数（不另打断点，F3）。"""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def _has_tool_history(msgs: list[Message]) -> bool:
    """消息历史中是否含工具调用/结果回合（用于决定是否关闭 thinking）。"""
    return any(m.tool_calls or m.tool_results for m in msgs)


def _to_anthropic_messages(msgs: list[Message]) -> list[dict]:
    """把协议无关 ``Message`` 列表转为 anthropic messages 参数。

    - assistant 工具调用回合：content 用 ``text`` + ``tool_use`` 块数组；
    - ``ROLE_TOOL`` 结果回合：所有 ``tool_result`` 拼进一条 user 消息的 content 数组。
    """
    out: list[dict] = []
    for m in msgs:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if not m.tool_calls:
                out.append({"role": "assistant", "content": m.content})
            else:
                content: list[dict] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": c.id,
                            "name": c.name,
                            "input": json.loads(c.input) if c.input else {},
                        }
                    )
                out.append({"role": "assistant", "content": content})
        else:  # ROLE_TOOL
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in m.tool_results
            ]
            out.append({"role": "user", "content": content})
    return out


def _append_reminder_anthropic(messages: list[dict], reminder: str) -> None:
    """把 reminder 文本块并入末条 user 消息的 content（避免连续 user 触发 400，N3）。

    末条非 user（如 assistant）时新起一条 user 消息承载 reminder。user 文本回合的
    str content 先转为块列表，再追加 reminder 文本块。
    """
    if not messages or messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": [{"type": "text", "text": reminder}]})
        return
    last = messages[-1]
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}] if last["content"] else []
    last["content"].append({"type": "text", "text": reminder})


def _build_anthropic_system(stable: str, environment: str) -> list[dict]:
    """构造 anthropic system 入参：稳定块带 ``cache_control: ephemeral`` 断点、
    环境块不带（F3/AC4）。二者分属不同内容块，缓存前缀 = 全部工具 + 稳定块。"""
    blocks: list[dict] = []
    if stable:
        blocks.append({"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}})
    if environment:
        blocks.append({"type": "text", "text": environment})
    return blocks


class AnthropicProvider:
    """anthropic 协议的 Provider 实现。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._client = anthropic.AsyncAnthropic(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
        )

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        # system 分两块：稳定块打缓存断点、环境块不打（F3）。
        system_blocks = _build_anthropic_system(req.system.stable, req.system.environment)
        messages = _to_anthropic_messages(req.messages)
        if req.reminder:
            _append_reminder_anthropic(messages, req.reminder)
        params: dict = {
            "model": self._cfg.model,
            "max_tokens": _MAX_TOKENS,
            "messages": messages,
        }
        if system_blocks:
            params["system"] = system_blocks
        tool_defs = _to_anthropic_tools(req.tools)
        if tool_defs:
            params["tools"] = tool_defs
        # 含工具历史的请求关闭 thinking：回灌带 tool_use 的 assistant 回合时，
        # Anthropic 要求附原 thinking 块（含 signature），而本章按 spec 丢弃
        # thinking 增量、不留签名，故对这类请求关闭 thinking 以避免 400。
        if self._cfg.thinking and not _has_tool_history(req.messages):
            params["thinking"] = {"type": "enabled", "budget_tokens": _THINKING_BUDGET}

        try:
            async with self._client.messages.stream(**params) as s:
                async for event in s:
                    if event.type == "content_block_delta":
                        # 仅取正文文本增量；thinking_delta / input_json_delta 跳过
                        # （input JSON 由 SDK 内部累加，流结束后从 final_message 取）。
                        if event.delta.type == "text_delta":
                            yield StreamEvent(text=event.delta.text)
                final_message = await s.get_final_message()
                calls: list[ToolCall] = []
                if final_message.stop_reason == "tool_use":
                    for block in final_message.content:
                        if block.type == "tool_use":
                            calls.append(
                                ToolCall(
                                    id=block.id,
                                    name=block.name,
                                    input=json.dumps(block.input),
                                )
                            )
                usage = Usage(
                    input_tokens=final_message.usage.input_tokens,
                    output_tokens=final_message.usage.output_tokens,
                    cache_write=getattr(final_message.usage, "cache_creation_input_tokens", 0) or 0,
                    cache_read=getattr(final_message.usage, "cache_read_input_tokens", 0) or 0,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 任意运行时错误均转为 err 事件
            yield StreamEvent(err=e)
            return
        if calls:
            yield StreamEvent(tool_calls=calls)
        yield StreamEvent(usage=usage)
        yield StreamEvent(done=True)
