"""Agent ReAct 循环编排（F1/F2/F5/F6/F10/AC1-AC4/AC8/AC9/AC13）。

每一轮：带工具定义发起流式请求 → 收集本轮文本/工具调用/用量 → 若模型请求了
工具则保序分批（连续只读并发、其余串行）执行并把结果回灌进历史，进入下一轮；
若模型给出无工具调用的纯文本，该文本即最终答复，循环结束。

停止条件（各自干净收尾，保持历史合法）：自然完成、迭代上限、用户取消、
连续多轮只请求未知工具、流出错。对外只吐 ``Event`` 异步流，供 TUI 渲染，
不暴露循环内部细节。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from koyocode.conversation import Conversation
from koyocode.llm import ROLE_ASSISTANT, Provider, ToolCall, ToolDefinition, ToolResult
from koyocode.llm import Usage as LLMUsage
from koyocode.prompt import PLAN_MODE_REMINDER
from koyocode.tool import DEFAULT_TIMEOUT, Registry

_ARGS_PREVIEW_LEN = 80
_EMPTY_FINAL_TEXT = "（无更多说明）"

MAX_ITERATIONS = 25
"""迭代上限兜底，避免失控（F2）。"""
MAX_UNKNOWN_RUN = 3
"""连续「整轮只产生未知工具调用」的迭代数上限（F2）。"""

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"


class Phase(Enum):
    """工具调用执行阶段。"""

    START = "start"
    END = "end"


class Mode(IntEnum):
    """Agent Loop 的两种工具集/系统提示模式（F10）。"""

    NORMAL = 0
    PLAN = 1


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""

    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Usage:
    """一轮请求的 token 用量（语义同 ``llm.Usage``，Agent 对外事件用此类型）。"""

    input: int = 0
    output: int = 0


@dataclass
class Event:
    """Agent Loop 对外事件流元素，TUI 据非默认字段分派渲染。"""

    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    iter: int = 0
    notice: str = ""
    done: bool = False
    err: Exception | None = None


def _preview_args(input_str: str) -> str:
    """工具参数预览：截断到约 80 字符。"""
    s = input_str.strip()
    if len(s) <= _ARGS_PREVIEW_LEN:
        return s
    return s[: _ARGS_PREVIEW_LEN - 3] + "..."


def _ensure_final(text: str) -> str:
    """自然完成的最终文本：非空原样返回，为空则给占位提示（避免空 assistant 回合）。"""
    return text if text else _EMPTY_FINAL_TEXT


def _ensure_assistant_tail(conv: Conversation, fallback: str) -> None:
    """保证历史以 assistant 文本回合收尾，下一轮请求不因悬空 tool_use / 连续同角色报错（F6）。"""
    if conv.last_role() != ROLE_ASSISTANT:
        conv.add_assistant(fallback)


def _all_unknown(registry: Registry, calls: list[ToolCall]) -> bool:
    """本轮工具调用是否全部未注册；混入任一已注册工具即 False（视为有进展）。"""
    return all(registry.get(c.name) is None for c in calls)


@dataclass
class _StreamOutcome:
    """``_stream_once`` 的返回值载体（async generator 无法直接 return 值）。"""

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: LLMUsage | None = None
    ok: bool = True


async def _stream_once(
    provider: Provider,
    conv: Conversation,
    defs: list[ToolDefinition],
    suffix: str,
    cancel: asyncio.Event,
    outcome: _StreamOutcome,
) -> AsyncIterator[Event]:
    """单次流式请求；把结果写入 ``outcome``，转发途中产生的文本增量 / 错误事件。"""
    try:
        async for se in provider.stream(conv.messages(), defs, suffix):
            if cancel.is_set():
                outcome.ok = False
                return
            if se.err is not None:
                yield Event(err=se.err)
                outcome.ok = False
                return
            if se.usage is not None:
                outcome.usage = se.usage
            if se.tool_calls:
                outcome.calls.extend(se.tool_calls)
            if se.text:
                outcome.text += se.text
                yield Event(text=se.text)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 任意运行时错误转为 err 事件
        yield Event(err=e)
        outcome.ok = False
        return
    if cancel.is_set():
        outcome.ok = False


async def _watched_execute(
    registry: Registry, call: ToolCall, cancel: asyncio.Event
) -> tuple[ToolResult, bool]:
    """执行单个工具调用，若期间 ``cancel`` 被 set 则尽快取消并返回「已取消」结果。

    返回 ``(result, completed)``；``completed=False`` 表示因取消而未跑完。
    """
    exec_task: asyncio.Task = asyncio.ensure_future(
        registry.execute(call.name, call.input, DEFAULT_TIMEOUT)
    )
    cancel_task: asyncio.Task = asyncio.ensure_future(cancel.wait())
    try:
        done, _pending = await asyncio.wait(
            {exec_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if exec_task in done:
            r = exec_task.result()
            return ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error), True
        exec_task.cancel()
        with contextlib.suppress(BaseException):
            await exec_task
        return (
            ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True),
            False,
        )
    finally:
        if not cancel_task.done():
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task


def _fill_cancelled(
    results: list[ToolResult | None], calls: list[ToolCall], start: int, end: int
) -> None:
    """给尚未执行（``results[k] is None``）的调用填「已取消」结构化结果。"""
    for k in range(start, end):
        if results[k] is None:
            results[k] = ToolResult(
                tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
            )


@dataclass
class _BatchOutcome:
    """``_execute_batched`` 的返回值载体。"""

    results: list[ToolResult] = field(default_factory=list)
    completed: bool = True


async def _execute_batched(
    registry: Registry,
    calls: list[ToolCall],
    cancel: asyncio.Event,
    outcome: _BatchOutcome,
) -> AsyncIterator[Event]:
    """保序分批执行：连续只读并发、其余串行，保持模型给出的相对顺序（F5）。

    开始事件按调用序发出、结束事件按调用序发出；并发只发生在执行环节，
    scrollback 顺序不受影响（N3）。每个并发 task 只写自己下标，无数据竞争（N6）。
    """
    n = len(calls)
    results: list[ToolResult | None] = [None] * n
    i = 0
    while i < n:
        if cancel.is_set():
            _fill_cancelled(results, calls, i, n)
            outcome.results = [r for r in results if r is not None]
            outcome.completed = False
            return

        j = i + 1
        if registry.is_read_only(calls[i].name):
            while j < n and registry.is_read_only(calls[j].name):
                j += 1

        for k in range(i, j):
            yield Event(
                tool=ToolEvent(
                    name=calls[k].name, args=_preview_args(calls[k].input), phase=Phase.START
                )
            )

        outs = await asyncio.gather(
            *(_watched_execute(registry, calls[k], cancel) for k in range(i, j))
        )
        segment_cancelled = False
        for offset, (result, completed) in enumerate(outs):
            results[i + offset] = result
            if not completed:
                segment_cancelled = True

        for k in range(i, j):
            r = results[k]
            assert r is not None
            yield Event(
                tool=ToolEvent(
                    name=calls[k].name,
                    args=_preview_args(calls[k].input),
                    phase=Phase.END,
                    result=r.content,
                    is_error=r.is_error,
                )
            )

        if segment_cancelled:
            _fill_cancelled(results, calls, j, n)
            outcome.results = [r for r in results if r is not None]
            outcome.completed = False
            return
        i = j

    outcome.results = [r for r in results if r is not None]
    outcome.completed = True


class Agent:
    """持有 provider 与注册中心，执行 ReAct 循环。"""

    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(
        self, conv: Conversation, mode: Mode, cancel: asyncio.Event
    ) -> AsyncIterator[Event]:
        """执行 Agent Loop，async generator 吐出事件流。

        ``mode`` 决定工具集与系统提示后缀；``cancel`` 由调用方持有的
        ``asyncio.Event``，触发 ``cancel.set()`` 即中断本轮（尽快收尾、
        保持历史合法，可继续对话）。
        """
        if mode == Mode.PLAN:
            defs = self._registry.read_only_definitions()
            suffix = PLAN_MODE_REMINDER
        else:
            defs = self._registry.definitions()
            suffix = ""

        unknown_run = 0
        for it in range(1, MAX_ITERATIONS + 1):
            yield Event(iter=it)
            if cancel.is_set():
                _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                return

            stream_outcome = _StreamOutcome()
            async for ev in _stream_once(
                self._provider, conv, defs, suffix, cancel, stream_outcome
            ):
                yield ev

            if not stream_outcome.ok:
                if cancel.is_set():
                    _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                else:
                    _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return

            if stream_outcome.usage is not None:
                yield Event(
                    usage=Usage(
                        input=stream_outcome.usage.input_tokens,
                        output=stream_outcome.usage.output_tokens,
                    )
                )

            if not stream_outcome.calls:
                final = _ensure_final(stream_outcome.text)
                if not stream_outcome.text:
                    yield Event(text=final)
                conv.add_assistant(final)
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(stream_outcome.text, stream_outcome.calls)
            unknown_run = (
                unknown_run + 1 if _all_unknown(self._registry, stream_outcome.calls) else 0
            )

            batch_outcome = _BatchOutcome()
            async for ev in _execute_batched(
                self._registry, stream_outcome.calls, cancel, batch_outcome
            ):
                yield ev
            conv.add_tool_results(batch_outcome.results)

            if not batch_outcome.completed:
                _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                _ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                yield Event(done=True)
                return

        yield Event(notice=NOTICE_MAX_ITER)
        _ensure_assistant_tail(conv, NOTICE_MAX_ITER)
        yield Event(done=True)


__all__ = [
    "MAX_ITERATIONS",
    "MAX_UNKNOWN_RUN",
    "NOTICE_CANCELLED",
    "NOTICE_MAX_ITER",
    "NOTICE_STREAM_ERR",
    "NOTICE_UNKNOWN_TOOLS",
    "Agent",
    "Event",
    "Mode",
    "Phase",
    "ToolEvent",
    "Usage",
]
