"""Agent 单轮闭环编排（F5/F6/AC9）。

请求#1（带工具）→ 收集工具调用 → 注册中心执行 → 结果回灌进 ``Conversation``
→ 请求#2（续答）→ 最终文本 → 停。对外吐出 ``Event`` 异步流供 TUI 渲染。

单轮上限（AC9）：请求#2 返回的工具调用被忽略，不再发起新一轮执行。
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

from koyocode.conversation import Conversation
from koyocode.llm import Provider, ToolCall, ToolResult
from koyocode.tool import DEFAULT_TIMEOUT, Registry

_ARGS_PREVIEW_LEN = 80
_EMPTY_FINAL_PROMPT = "（单轮工具调用上限：本章不再发起新一轮工具调用）"


class Phase(Enum):
    """工具调用执行阶段。"""

    START = "start"
    END = "end"


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""

    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Event:
    """单轮闭环对外事件流元素，TUI 据非 None 字段分派渲染。"""

    text: str = ""
    tool: ToolEvent | None = None
    done: bool = False
    err: Exception | None = None


def _preview_args(input_str: str) -> str:
    """工具参数预览：截断到约 80 字符。"""
    s = input_str.strip()
    if len(s) <= _ARGS_PREVIEW_LEN:
        return s
    return s[: _ARGS_PREVIEW_LEN - 3] + "..."


class Agent:
    """持有 provider 与注册中心，执行单轮闭环。"""

    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def _stream_once(
        self, conv: Conversation, defs: list
    ) -> AsyncIterator[tuple[str, object]]:
        """单次流式请求；yield ``("text", str)`` / ``("calls", list)`` / ``("err", Exception)``。"""
        try:
            async for se in self._provider.stream(conv.messages(), defs):
                if se.err is not None:
                    yield ("err", se.err)
                    return
                if se.text:
                    yield ("text", se.text)
                if se.tool_calls:
                    yield ("calls", se.tool_calls)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 任意运行时错误转为 err 事件
            yield ("err", e)

    async def run(self, conv: Conversation) -> AsyncIterator[Event]:
        """执行单轮闭环，async generator 吐出事件流。

        调用方 cancel() 此 task（退出/Ctrl+C）时 ``async for`` 抛 ``CancelledError``
        终止；工具执行经 ``asyncio.wait_for`` 受 ``DEFAULT_TIMEOUT`` 约束（N1）。
        """
        defs = self._registry.definitions()

        # ── 请求#1：带工具 ──
        preamble = ""
        calls: list[ToolCall] = []
        async for kind, payload in self._stream_once(conv, defs):
            if kind == "text":
                preamble += payload  # type: ignore[assignment]
                yield Event(text=payload)  # type: ignore[arg-type]
            elif kind == "calls":
                calls.extend(payload)  # type: ignore[arg-type]
            else:  # err
                yield Event(err=payload)  # type: ignore[arg-type]
                return

        if not calls:
            conv.add_assistant(preamble)
            yield Event(done=True)
            return

        conv.add_assistant_with_tool_calls(preamble, calls)
        # 顺序执行每个工具调用
        results: list[ToolResult] = []
        for call in calls:
            args_preview = _preview_args(call.input)
            yield Event(tool=ToolEvent(name=call.name, args=args_preview, phase=Phase.START))
            r = await self._registry.execute(call.name, call.input, DEFAULT_TIMEOUT)
            yield Event(
                tool=ToolEvent(
                    name=call.name,
                    args=args_preview,
                    phase=Phase.END,
                    result=r.content,
                    is_error=r.is_error,
                )
            )
            results.append(ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error))
        conv.add_tool_results(results)

        # ── 请求#2：续答（忽略其工具调用，单轮 AC9）──
        final = ""
        async for kind, payload in self._stream_once(conv, defs):
            if kind == "text":
                final += payload  # type: ignore[assignment]
                yield Event(text=payload)  # type: ignore[arg-type]
            elif kind == "err":
                yield Event(err=payload)  # type: ignore[arg-type]
                return
            # "calls" 忽略：单轮上限，不再执行

        if not final:
            # 空 assistant 回合会破坏下一轮请求（角色交替 + 非空要求）；
            # 占位提示同时作为 AC9 的单轮上限提示。
            final = _EMPTY_FINAL_PROMPT
            yield Event(text=final)
        conv.add_assistant(final)
        yield Event(done=True)


__all__ = ["Agent", "Event", "Phase", "ToolEvent"]
