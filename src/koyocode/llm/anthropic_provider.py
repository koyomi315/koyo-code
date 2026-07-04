"""anthropic 协议适配器：封装 ``AsyncAnthropic``，统一吐出 ``StreamEvent``。

- 注入内置 system prompt；按 ``cfg.thinking`` 开启扩展思考。
- 流式迭代使用 ``stream.text_stream``，仅取正文文本增量；
  thinking 增量自动被跳过（接收即丢弃），不混入正文。
- 异常转为 ``StreamEvent(err=...)``；``CancelledError`` 透传以支持 task 取消。
"""

import asyncio
from collections.abc import AsyncIterator

import anthropic

from koyocode.config import ProviderConfig
from koyocode.llm import Message, StreamEvent
from koyocode.prompt import SYSTEM_PROMPT

_MAX_TOKENS = 4096
_THINKING_BUDGET = 2048


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

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        params: dict = {
            "model": self._cfg.model,
            "max_tokens": _MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }
        if self._cfg.thinking:
            params["thinking"] = {"type": "enabled", "budget_tokens": _THINKING_BUDGET}

        try:
            async with self._client.messages.stream(**params) as s:
                # text_stream 仅产出正文文本增量；thinking 增量被跳过。
                async for text in s.text_stream:
                    yield StreamEvent(text=text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 任意运行时错误均转为 err 事件
            yield StreamEvent(err=e)
            return
        yield StreamEvent(done=True)
