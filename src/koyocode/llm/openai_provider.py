"""openai 协议适配器：封装 ``AsyncOpenAI``，统一吐出 ``StreamEvent``。

- 在消息列表首条注入内置 system prompt；``thinking`` 字段忽略。
- ``cfg.base_url`` 非空时覆盖默认端点，可接入各类兼容服务。
- 流式迭代 ``chunk.choices[0].delta.content``；异常转为 ``err`` 事件。
"""

import asyncio
from collections.abc import AsyncIterator

import openai
from openai.types.chat import ChatCompletionMessageParam

from koyocode.config import ProviderConfig
from koyocode.llm import Message, StreamEvent
from koyocode.prompt import SYSTEM_PROMPT


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

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            # role 为运行期 Literal["user","assistant"]，openai SDK 的 TypedDict
            # 联合无法静态收窄动态 role，此处按已知合法消息标注。
            *({"role": m.role, "content": m.content} for m in msgs),  # type: ignore[list-item]
        ]
        try:
            stream = await self._client.chat.completions.create(
                model=self._cfg.model,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield StreamEvent(text=delta)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 任意运行时错误均转为 err 事件
            yield StreamEvent(err=e)
            return
        yield StreamEvent(done=True)
