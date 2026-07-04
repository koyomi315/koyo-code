"""openai 协议适配器：封装 ``AsyncOpenAI``，统一吐出 ``StreamEvent``。

- 在消息列表首条注入内置 system prompt；``thinking`` 字段忽略。
- ``cfg.base_url`` 非空时覆盖默认端点，可接入各类兼容服务。
- 工具调用全流程（F3/F4/F6/F7）：
  - 请求注入 ``tools``（``_to_openai_tools``）；
  - 流式按 ``delta.tool_calls[i].index`` 累加分片，``delta.content`` 仍吐文本增量；
  - 流结束后按 index 排序组装 ``ToolCall``（空 arguments 归一为 ``"{}"``）；
  - ``_to_openai_messages`` 把 assistant 工具调用回合发为带 ``tool_calls`` 的 assistant
    消息、``ROLE_TOOL`` 结果回合每个 ``ToolResult`` 发一条 ``role=tool`` 消息。
- 异常转为 ``err`` 事件；``CancelledError`` 透传以支持 task 取消。
"""

import asyncio
from collections.abc import AsyncIterator

import openai

from koyocode.config import ProviderConfig
from koyocode.llm import Message, StreamEvent, ToolCall, ToolDefinition
from koyocode.prompt import SYSTEM_PROMPT


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict]:
    """把协议无关 ``ToolDefinition`` 转为 openai tools 参数。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _to_openai_messages(msgs: list[Message]) -> list[dict]:
    """把协议无关 ``Message`` 列表转为 openai messages 参数（首条为 system）。"""
    out: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in msgs:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if not m.tool_calls:
                out.append({"role": "assistant", "content": m.content})
            else:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": c.input or "{}"},
                            }
                            for c in m.tool_calls
                        ],
                    }
                )
        else:  # ROLE_TOOL：每个结果发一条 role=tool 消息
            for r in m.tool_results:
                out.append({"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content})
    return out


class OpenAIProvider:
    """openai 协议的 Provider 实现。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._client = openai.AsyncOpenAI(
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
        messages = _to_openai_messages(msgs)
        try:
            create_kwargs: dict = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
            }
            tool_defs = _to_openai_tools(tools)
            if tool_defs:
                create_kwargs["tools"] = tool_defs
            stream = await self._client.chat.completions.create(**create_kwargs)
            # 按 index 累加 tool_calls 分片；delta.content 仍吐文本增量。
            tool_calls_buf: dict[int, dict[str, str]] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield StreamEvent(text=delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        slot = tool_calls_buf.setdefault(idx, {})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] = slot.get("args", "") + tc.function.arguments
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 任意运行时错误均转为 err 事件
            yield StreamEvent(err=e)
            return
        # 流结束：按 index 排序组装工具调用（空 arguments 归一为 "{}"）
        calls = [
            ToolCall(
                id=tool_calls_buf[i].get("id", ""),
                name=tool_calls_buf[i].get("name", ""),
                input=tool_calls_buf[i].get("args") or "{}",
            )
            for i in sorted(tool_calls_buf)
        ]
        if calls:
            yield StreamEvent(tool_calls=calls)
        yield StreamEvent(done=True)
