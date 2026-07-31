"""openai 协议适配器：封装 ``AsyncOpenAI``，统一吐出 ``StreamEvent``。

- 系统提示拼为单条 system 消息（F3/F8）：``stable`` 在前、``environment`` 在后，stable
  居前缀使端点前缀缓存命中稳定部分（兼容端点对多条 system 支持不一，统一单条）。
- reminder 织入消息通道（F6/N3）：非空时追加一条尾部 user 消息（OpenAI 容忍连续 user/tool）。
- 工具调用全流程：请求注入 ``tools``；流式按 ``delta.tool_calls[i].index`` 累加分片；
  流结束后按 index 排序组装 ``ToolUseBlock``（空 arguments 归一为 ``"{}"``）。
- 缓存用量解析（F4/N6）：``prompt_tokens_details.cached_tokens``，``cache_write`` 恒 0，缺字段为 0。
- 异常转为 ``err`` 事件；``CancelledError`` 透传以支持 task 取消。
"""

import asyncio
from collections.abc import AsyncIterator

import openai

from koyocode.config import ProviderConfig
from koyocode.llm import (
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolDefinition,
    ToolUseBlock,
    Usage,
)


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


def _to_openai_messages(req: Request) -> list[dict]:
    """把 ``Request`` 转为 openai messages 参数。

    首条 system = ``stable``（若 ``environment`` 非空则拼为 ``stable + "\\n\\n" + environment``，
    stable 居前缀）；``reminder`` 非空时追加尾部 user 消息。
    """
    system = "\n\n".join(p for p in (req.system.stable, req.system.environment) if p)
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in req.messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if not m.tool_uses:
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
                            for c in m.tool_uses
                        ],
                    }
                )
        else:  # ROLE_TOOL：每个结果发一条 role=tool 消息
            for r in m.tool_results:
                out.append({"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content})
    if req.reminder:
        out.append({"role": "user", "content": req.reminder})
    return out


def _is_context_length_exceeded_openai(e: Exception) -> bool:
    """openai BadRequestError 是否为上下文超长。"""
    text = str(e).lower()
    return "context_length_exceeded" in text or "context length" in text


def _wrap_openai_err(e: openai.BadRequestError) -> Exception:
    """openai 400 命中上下文超长时包装为 PromptTooLongError，否则原样返回。"""
    if _is_context_length_exceeded_openai(e):
        wrapped: Exception = PromptTooLongError("openai prompt too long")
        wrapped.__cause__ = e
        return wrapped
    return e


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

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        messages = _to_openai_messages(req)
        usage: Usage | None = None
        try:
            create_kwargs: dict = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            tool_defs = _to_openai_tools(req.tools)
            if tool_defs:
                create_kwargs["tools"] = tool_defs
            stream = await self._client.chat.completions.create(**create_kwargs)
            # 按 index 累加 tool_calls 分片；delta.content 仍吐文本增量。
            tool_calls_buf: dict[int, dict[str, str]] = {}
            async for chunk in stream:
                if not chunk.choices:
                    # include_usage 开启后，流末尾出现一个 choices 为空但带
                    # chunk.usage 的 chunk；此 chunk 无 delta 可读，跳过文本分支。
                    if chunk.usage is not None:
                        details = getattr(chunk.usage, "prompt_tokens_details", None)
                        cache_read = getattr(details, "cached_tokens", 0) or 0
                        usage = Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read=cache_read,
                            cache_write=0,
                        )
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
        except openai.BadRequestError as e:
            yield StreamEvent(err=_wrap_openai_err(e))
            return
        except Exception as e:  # noqa: BLE001 - 任意运行时错误转为 err 事件
            yield StreamEvent(err=e)
            return
        # 流结束：按 index 排序组装工具调用（空 arguments 归一为 "{}"）
        calls = [
            ToolUseBlock(
                id=tool_calls_buf[i].get("id", ""),
                name=tool_calls_buf[i].get("name", ""),
                input=tool_calls_buf[i].get("args") or "{}",
            )
            for i in sorted(tool_calls_buf)
        ]
        if calls:
            yield StreamEvent(tool_uses=calls)
        yield StreamEvent(usage=usage)
        yield StreamEvent(done=True)
