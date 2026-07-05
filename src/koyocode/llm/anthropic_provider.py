"""anthropic 协议适配器：封装 ``AsyncAnthropic``，统一吐出 ``StreamEvent``。

- 注入内置 system prompt；按 ``cfg.thinking`` 开启扩展思考。
- 工具调用全流程（F3/F4/F6/F7）：
  - 请求注入 ``tools``（``_to_anthropic_tools``）；
  - 流式按事件分派，仅取 ``text_delta`` 文本增量（thinking/input_json 增量跳过，
    input JSON 由 SDK 内部累加）；
  - 流结束后取 ``get_final_message``，若 ``stop_reason == "tool_use"`` 收集
    ``ToolUseBlock`` 组装 ``ToolCall`` 一次性上抛；
  - ``_to_anthropic_messages`` 把 assistant 工具调用回合映射为 ``tool_use`` content
    块、``ROLE_TOOL`` 结果回合映射为一条 user 消息的 ``tool_result`` content 数组。
- 含工具历史的请求关闭 thinking（避免 Anthropic 要求回灌 thinking 签名导致 400）。
- 异常转为 ``StreamEvent(err=...)``；``CancelledError`` 透传以支持 task 取消。
"""

import asyncio
import json
from collections.abc import AsyncIterator

import anthropic

from koyocode.config import ProviderConfig
from koyocode.llm import Message, StreamEvent, ToolCall, ToolDefinition
from koyocode.prompt import SYSTEM_PROMPT

_MAX_TOKENS = 4096
_THINKING_BUDGET = 2048


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
    """把协议无关 ``ToolDefinition`` 转为 anthropic tools 参数。"""
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

    async def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]:
        params: dict = {
            "model": self._cfg.model,
            "max_tokens": _MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": _to_anthropic_messages(msgs),
        }
        tool_defs = _to_anthropic_tools(tools)
        if tool_defs:
            params["tools"] = tool_defs
        # 含工具历史的请求关闭 thinking：回灌带 tool_use 的 assistant 回合时，
        # Anthropic 要求附原 thinking 块（含 signature），而本章按 spec 丢弃
        # thinking 增量、不留签名，故对这类请求关闭 thinking 以避免 400。
        if self._cfg.thinking and not _has_tool_history(msgs):
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
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 任意运行时错误均转为 err 事件
            yield StreamEvent(err=e)
            return
        if calls:
            yield StreamEvent(tool_calls=calls)
        yield StreamEvent(done=True)
