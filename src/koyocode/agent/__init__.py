"""Agent ReAct 循环编排（F1/F2/F5/F6/F10/AC1-AC4/AC8/AC9/AC13）。

每一轮：带工具定义发起流式请求 -> 收集本轮文本/工具调用/用量 -> 若模型请求了
工具则保序分批（连续只读并发、其余串行）执行并把结果回灌进历史，进入下一轮；
若模型给出无工具调用的纯文本，该文本即最终答复，循环结束。

ch05 扩展：每次 ``run`` 起始采集环境、装配稳定系统提示；每轮按 ``mode + iter``
计算 reminder（规划模式按轮次详略，F7），组装 ``llm.Request`` 发起请求；缓存用量
透传到 ``Event.usage``。稳定系统提示普通/规划一致（规划提醒已移出系统通道）。

ch06 扩展（权限系统）：``Mode`` 迁至 ``permission`` 模块（四档）；``Agent`` 持有
``permission.Engine``；``execute_batched`` 在执行每个工具前调用 ``engine.check``——
Allow 执行、Deny 直接产被拒 ``ToolResultBlock``、Ask 发 ``ApprovalRequest`` 事件并
``await`` 用户三选一决策（第五层人在回路，由本模块编排驱动）。

停止条件（各自干净收尾，保持历史合法）：自然完成、迭代上限、用户取消、
连续多轮只请求未知工具、流出错。对外只吐 ``Event`` 异步流，供 TUI 渲染，
不暴露循环内部细节。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from koyocode import prompt
from koyocode.agent.event import CompactEvent, CompactPhase
from koyocode.agent.runtime import SessionRuntime
from koyocode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    estimate_tokens,
    manage_context,
    new_session_context,
    usage_anchor,
)
from koyocode.compact.const import AUTO_SAFETY_MARGIN, MANUAL_SAFETY_MARGIN, SUMMARY_RESERVE
from koyocode.conversation import Conversation
from koyocode.llm import (
    ROLE_ASSISTANT,
    PromptTooLongError,
    Provider,
    Request,
    System,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from koyocode.llm import Usage as LLMUsage
from koyocode.permission import Decision, Engine, Mode, Outcome
from koyocode.tool import DEFAULT_TIMEOUT, Registry

log = logging.getLogger(__name__)

_ARGS_PREVIEW_LEN = 80
_EMPTY_FINAL_TEXT = "（无更多说明）"

MAX_ITERATIONS = 25
"""迭代上限兜底，避免失控（F2）。"""
MAX_UNKNOWN_RUN = 3
"""连续「整轮只产生未知工具调用」的迭代数上限（F2）。"""
PLAN_REMINDER_INTERVAL = 4
"""规划模式完整提醒的重复间隔轮次（F7）：首轮完整，之后每隔此数重复一次。"""

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"


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
class Usage:
    """一轮请求的 token 用量（含缓存写/读，透传自 ``llm.Usage``，供 smoke/TUI 打印）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class ApprovalRequest:
    """人在回路请求（第五层）：agent emit 后 ``await respond`` 等用户三选一。

    ``respond`` 为单次 ``asyncio.Future[Outcome]``；TUI 调 ``set_result(Outcome)`` 后
    agent 从 ``await`` 恢复。取消时上层兜底 ``set_result(Outcome.DENY_ONCE)``，
    本协程经 ``asyncio.CancelledError`` 退出。
    """

    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]


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
    approval: ApprovalRequest | None = None
    compact: CompactEvent | None = None


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


def _all_unknown(registry: Registry, calls: list[ToolUseBlock]) -> bool:
    """本轮工具调用是否全部未注册；混入任一已注册工具即 False（视为有进展）。"""
    return all(registry.get(c.name) is None for c in calls)


@dataclass
class _StreamOutcome:
    """``_stream_once`` 的返回值载体（async generator 无法直接 return 值）。"""

    text: str = ""
    calls: list[ToolUseBlock] = field(default_factory=list)
    usage: LLMUsage | None = None
    ok: bool = True
    err: Exception | None = None


async def _stream_once(
    provider: Provider,
    conv: Conversation,
    defs: list[ToolDefinition],
    sys_text: str,
    env_text: str,
    reminder: str,
    cancel: asyncio.Event,
    outcome: _StreamOutcome,
) -> AsyncIterator[Event]:
    """单次流式请求；把结果写入 ``outcome``，转发途中产生的文本增量 / 错误事件。"""
    try:
        req = Request(
            messages=conv.messages(),
            tools=defs,
            system=System(stable=sys_text, environment=env_text),
            reminder=reminder,
        )
        async for se in provider.stream(req):
            if cancel.is_set():
                outcome.ok = False
                return
            if se.err is not None:
                outcome.err = se.err
                outcome.ok = False
                return
            if se.usage is not None:
                outcome.usage = se.usage
            if se.tool_uses:
                outcome.calls.extend(se.tool_uses)
            if se.text:
                outcome.text += se.text
                yield Event(text=se.text)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - 任意运行时错误转为 err 事件
        outcome.err = e
        outcome.ok = False
        return
    if cancel.is_set():
        outcome.ok = False


def _end_event(calls: list[ToolUseBlock], results: list[ToolResultBlock | None], k: int) -> Event:
    """构造第 ``k`` 个调用的 ``Phase.END`` 事件（保序回灌，含被拒/取消项）。"""
    r = results[k]
    assert r is not None
    return Event(
        tool=ToolEvent(
            name=calls[k].name,
            args=_preview_args(calls[k].input),
            phase=Phase.END,
            result=r.content,
            is_error=r.is_error,
        )
    )


def _finish_segment_cancelled(
    results: list[ToolResultBlock | None], calls: list[ToolUseBlock], i: int, j: int, n: int
) -> None:
    """本批有取消：给 [i,j) 之后到 n 的未执行项填取消结果；[i,j) 内由调用方先填好。"""
    _fill_cancelled(results, calls, j, n)
    # 兜底 [i,j) 中仍为 None 的（理论上不应有）
    _fill_cancelled(results, calls, i, j)


async def _watched_execute(
    registry: Registry, call: ToolUseBlock, cancel: asyncio.Event
) -> tuple[ToolResultBlock, bool]:
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
            return ToolResultBlock(
                tool_call_id=call.id, content=r.content, is_error=r.is_error
            ), True
        exec_task.cancel()
        with contextlib.suppress(BaseException):
            await exec_task
        return (
            ToolResultBlock(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True),
            False,
        )
    finally:
        if not cancel_task.done():
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task


def _fill_cancelled(
    results: list[ToolResultBlock | None], calls: list[ToolUseBlock], start: int, end: int
) -> None:
    """给尚未执行（``results[k] is None``）的调用填「已取消」结构化结果。"""
    for k in range(start, end):
        if results[k] is None:
            results[k] = ToolResultBlock(
                tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
            )


@dataclass
class _BatchOutcome:
    """``_execute_batched`` 的返回值载体。"""

    results: list[ToolResultBlock] = field(default_factory=list)
    completed: bool = True


async def _execute_batched(
    agent: Agent,
    registry: Registry,
    calls: list[ToolUseBlock],
    mode: Mode,
    cancel: asyncio.Event,
    outcome: _BatchOutcome,
) -> AsyncIterator[Event]:
    """保序分批执行：连续只读并发、其余串行，保持模型给出的相对顺序（F5）。

    每个工具执行前调用 ``agent.engine.check(mode, call, read_only)``：

    - **只读批**：逐个 check；Deny 预置被拒 ``ToolResultBlock`` 不入并发；Allow 照旧并发
      （只读永不 Ask，N3 并发不退化；若意外 Ask 按 Deny 兜底安全）。
    - **有副作用串行**：Allow→执行；Deny→被拒结果；Ask→``request_approval`` 等用户三选一。

    开始/结束事件按调用序发出（Deny 项也发 ``PhaseEnd``、``is_error=True``，与有副作用
    Deny 一致）。每个并发 task 只写自己下标，无数据竞争（N6）。
    """
    n = len(calls)
    results: list[ToolResultBlock | None] = [None] * n
    i = 0
    while i < n:
        if cancel.is_set():
            _fill_cancelled(results, calls, i, n)
            outcome.results = [r for r in results if r is not None]
            outcome.completed = False
            return

        # 切片：连续只读成一批，其余单个成批
        j = i + 1
        if registry.is_read_only(calls[i].name):
            while j < n and registry.is_read_only(calls[j].name):
                j += 1
        read_only_batch = j - i > 1 or registry.is_read_only(calls[i].name)

        for k in range(i, j):
            yield Event(
                tool=ToolEvent(
                    name=calls[k].name, args=_preview_args(calls[k].input), phase=Phase.START
                )
            )

        if read_only_batch:
            # 只读批：逐个 check；Deny 预置被拒结果不入并发
            deny_set: set[int] = set()
            for k in range(i, j):
                decision, reason = agent.engine.check(mode, calls[k], True)
                if decision == Decision.DENY:
                    results[k] = ToolResultBlock(
                        tool_call_id=calls[k].id, content=reason, is_error=True
                    )
                    deny_set.add(k)
                elif decision == Decision.ASK:
                    # 只读永不 Ask；兜底安全拒绝
                    results[k] = ToolResultBlock(
                        tool_call_id=calls[k].id,
                        content="只读工具被要求确认，安全拒绝",
                        is_error=True,
                    )
                    deny_set.add(k)
            runnable = [k for k in range(i, j) if k not in deny_set]
            if runnable:
                outs = await asyncio.gather(
                    *(_watched_execute(registry, calls[k], cancel) for k in runnable)
                )
                segment_cancelled = False
                for offset, k in enumerate(runnable):
                    result, completed = outs[offset]
                    results[k] = result
                    if not completed:
                        segment_cancelled = True
                if segment_cancelled:
                    _finish_segment_cancelled(results, calls, i, j, n)
                    for k in range(i, j):
                        yield _end_event(calls, results, k)
                    outcome.results = [r for r in results if r is not None]
                    outcome.completed = False
                    return
        else:
            # 有副作用串行：单个调用
            k = i
            decision, reason = agent.engine.check(mode, calls[k], False)
            if decision == Decision.ALLOW:
                result, completed = await _watched_execute(registry, calls[k], cancel)
                results[k] = result
                if not completed:
                    _finish_segment_cancelled(results, calls, i, j, n)
                    for kk in range(i, j):
                        yield _end_event(calls, results, kk)
                    outcome.results = [r for r in results if r is not None]
                    outcome.completed = False
                    return
            elif decision == Decision.DENY:
                results[k] = ToolResultBlock(
                    tool_call_id=calls[k].id, content=reason, is_error=True
                )
            else:  # ASK：人在回路
                # 借助 _execute_batched 自身的 async generator：yield 出审批事件，
                # 供顶层消费者 set_result 后，批循环在此 await 恢复（见 run 透传）。
                respond: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
                approval_ev = Event(
                    approval=ApprovalRequest(
                        name=calls[k].name,
                        args=_preview_args(calls[k].input),
                        reason=reason,
                        respond=respond,
                    )
                )
                try:
                    yield approval_ev
                    outcome_req = await respond
                except asyncio.CancelledError:
                    if not respond.done():
                        respond.set_result(Outcome.DENY_ONCE)
                    raise
                if outcome_req == Outcome.ALLOW_ONCE:
                    result, completed = await _watched_execute(registry, calls[k], cancel)
                    results[k] = result
                    if not completed:
                        _finish_segment_cancelled(results, calls, i, j, n)
                        for kk in range(i, j):
                            yield _end_event(calls, results, kk)
                        outcome.results = [r for r in results if r is not None]
                        outcome.completed = False
                        return
                elif outcome_req == Outcome.ALLOW_FOREVER:
                    try:
                        agent.engine.persist_local_allow(calls[k])
                    except Exception as e:  # noqa: BLE001 - 永久写入失败仅记日志不阻断
                        log.warning("永久放行写入失败: %s", e)
                    result, completed = await _watched_execute(registry, calls[k], cancel)
                    results[k] = result
                    if not completed:
                        _finish_segment_cancelled(results, calls, i, j, n)
                        for kk in range(i, j):
                            yield _end_event(calls, results, kk)
                        outcome.results = [r for r in results if r is not None]
                        outcome.completed = False
                        return
                else:  # DENY_ONCE
                    results[k] = ToolResultBlock(
                        tool_call_id=calls[k].id,
                        content=reason or "用户拒绝执行该操作",
                        is_error=True,
                    )

        for k in range(i, j):
            r = results[k]
            assert r is not None
            yield _end_event(calls, results, k)

        i = j

    outcome.results = [r for r in results if r is not None]
    outcome.completed = True


class Agent:
    """持有 provider 与注册中心与权限引擎，执行 ReAct 循环。"""

    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str,
        engine: Engine,
        runtime: SessionRuntime | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self._engine = engine
        # runtime=None 时构造默认实例，保留对无 compact 场景与旧测试的兼容
        if runtime is None:
            runtime = SessionRuntime(
                replacement=ContentReplacementState(),
                recovery=RecoveryState(),
                auto_tracking=CompactCircuitBreaker(),
                session=new_session_context("."),
                context_window=200000,
            )
        self.runtime = runtime
        self._run_lock = asyncio.Lock()
        self._last_manage_output = None

    @property
    def engine(self) -> Engine:
        return self._engine

    def run(self, conv: Conversation, mode: Mode, cancel: asyncio.Event) -> AsyncIterator[Event]:
        """执行 Agent Loop，async generator 吐出事件流。"""
        return self._run_impl(conv, mode, cancel)

    async def _run_impl(
        self, conv: Conversation, mode: Mode, cancel: asyncio.Event
    ) -> AsyncIterator[Event]:
        async with self._run_lock:
            env = prompt.gather_environment(self._version, self._provider.model)
            sys_text = prompt.build_system_prompt()
            env_text = env.render()
            unknown_run = 0
            for it in range(1, MAX_ITERATIONS + 1):
                emergency_retried = False
                yield Event(iter=it)
                if cancel.is_set():
                    _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                    return

                # 本轮工具定义（mode 决定），恢复段与 stream 共用同一份引用
                if mode == Mode.PLAN:
                    defs = self._registry.read_only_definitions()
                else:
                    defs = self._registry.definitions()

                reminder = ""
                if mode == Mode.PLAN:
                    full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                    reminder = prompt.plan_reminder(full)

                # 上下文管理（AUTO：layer1 + 阈值判断 + 可能 layer2）
                try:
                    async for ev in self._manage_and_emit(conv, defs, TriggerKind.AUTO):
                        yield ev
                except Exception as err:  # noqa: BLE001
                    yield Event(err=err)
                    _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    return

                stream_outcome = _StreamOutcome()
                async for ev in _stream_once(
                    self._provider, conv, defs, sys_text, env_text, reminder, cancel, stream_outcome
                ):
                    yield ev

                # PTL 紧急压缩 + 同迭代重试一次（emergency_retried 锁定一次性）
                if (
                    not stream_outcome.ok
                    and not cancel.is_set()
                    and isinstance(stream_outcome.err, PromptTooLongError)
                    and not emergency_retried
                ):
                    emergency_retried = True
                    try:
                        async for ev in self._manage_and_emit(conv, defs, TriggerKind.EMERGENCY):
                            yield ev
                    except Exception as ferr:  # noqa: BLE001
                        yield Event(err=ferr)
                        _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                        return
                    self.runtime.usage_anchor = 0
                    self.runtime.anchor_msg_len = 0
                    est = estimate_tokens(0, conv.messages(), 0)
                    if est >= self.runtime.context_window - MANUAL_SAFETY_MARGIN:
                        yield Event(err=stream_outcome.err)
                        _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                        return
                    stream_outcome = _StreamOutcome()
                    async for ev in _stream_once(
                        self._provider,
                        conv,
                        defs,
                        sys_text,
                        env_text,
                        reminder,
                        cancel,
                        stream_outcome,
                    ):
                        yield ev

                if not stream_outcome.ok:
                    if cancel.is_set():
                        _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                    else:
                        if stream_outcome.err is not None:
                            yield Event(err=stream_outcome.err)
                        _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    return

                # 主对话路径 usage 更新（替换，非累加）；摘要请求不更新锚点
                if stream_outcome.usage is not None:
                    self.runtime.usage_anchor = usage_anchor(stream_outcome.usage)
                    self.runtime.anchor_msg_len = conv.length()
                    yield Event(
                        usage=Usage(
                            input=stream_outcome.usage.input_tokens,
                            output=stream_outcome.usage.output_tokens,
                            cache_write=stream_outcome.usage.cache_write,
                            cache_read=stream_outcome.usage.cache_read,
                        )
                    )

                if not stream_outcome.calls:
                    final = _ensure_final(stream_outcome.text)
                    if not stream_outcome.text:
                        yield Event(text=final)
                    conv.add_assistant(final)
                    yield Event(done=True)
                    return

                conv.add_assistant_with_tool_uses(stream_outcome.text, stream_outcome.calls)
                unknown_run = (
                    unknown_run + 1 if _all_unknown(self._registry, stream_outcome.calls) else 0
                )

                batch_outcome = _BatchOutcome()
                async for ev in _execute_batched(
                    self, self._registry, stream_outcome.calls, mode, cancel, batch_outcome
                ):
                    yield ev
                # ReadFile 追踪：add_tool_results 前用纯净字节记录到 recovery（F19）
                await self._track_read_files(stream_outcome.calls, batch_outcome.results)
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

    async def _manage_and_emit(
        self,
        conv: Conversation,
        defs: list[ToolDefinition],
        trigger: TriggerKind,
    ) -> AsyncIterator[Event]:
        """构造 ManageInput 调 manage_context，并在摘要前后 emit Compact 事件。

        AUTO 在估算超阈值时 emit BEFORE_AUTO/AFTER_AUTO；EMERGENCY 总是 emit
        BEFORE_EMERGENCY/AFTER_EMERGENCY。仅 layer1（未触发 layer2）时不 emit
        任何 Compact 事件（layer1 静默）。manage_context 异常在 emit 完 AFTER 后抛出。
        """
        anchor = self.runtime.usage_anchor
        anchor_len = self.runtime.anchor_msg_len
        cw = self.runtime.context_window
        est = estimate_tokens(anchor, conv.messages(), anchor_len)
        in_ = ManageInput(
            conv=conv,
            provider=self._provider,
            context_window=cw,
            tool_defs=defs,
            replacement=self.runtime.replacement,
            recovery=self.runtime.recovery,
            auto_tracking=self.runtime.auto_tracking,
            session=self.runtime.session,
            usage_anchor=anchor,
            anchor_msg_len=anchor_len,
            estimated_token=est,
            trigger=trigger,
        )
        will_auto = trigger == TriggerKind.AUTO and est >= cw - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
        if will_auto:
            yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO, before=est))
        if trigger == TriggerKind.EMERGENCY:
            yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
        try:
            out = await manage_context(in_)
            mc_err: Exception | None = None
        except Exception as e:  # noqa: BLE001
            out = None
            mc_err = e
        after = out.after_tokens if out is not None else 0
        if will_auto:
            yield Event(
                compact=CompactEvent(
                    phase=CompactPhase.AFTER_AUTO, before=est, after=after, err=mc_err
                )
            )
        if trigger == TriggerKind.EMERGENCY:
            yield Event(
                compact=CompactEvent(
                    phase=CompactPhase.AFTER_EMERGENCY, before=est, after=after, err=mc_err
                )
            )
        if mc_err is not None:
            raise mc_err

    async def _track_read_files(
        self, calls: list[ToolUseBlock], results: list[ToolResultBlock]
    ) -> None:
        """对成功的 read_file 调用，用纯净字节（不带行号）记录到 recovery（F19）。

        必须在 ``conv.add_tool_results`` 之前完成，保证下一次 ``manage_context``
        能观察到本轮 ReadFile 记录。读盘失败静默跳过。
        """
        for call, result in zip(calls, results, strict=True):
            if call.name != "read_file" or result.is_error:
                continue
            try:
                args = json.loads(call.input) if call.input else {}
            except json.JSONDecodeError:
                continue
            path = args.get("path") if isinstance(args, dict) else None
            if not isinstance(path, str) or not path:
                continue
            try:
                abs_path = str(Path(path).resolve())
                data = await asyncio.to_thread(Path(abs_path).read_bytes)
            except (OSError, ValueError):
                continue
            self.runtime.recovery.record_file(abs_path, data.decode("utf-8", errors="replace"))

    async def run_force_compact(
        self, conv: Conversation, tool_defs: list[ToolDefinition]
    ) -> tuple[int, int]:
        """手动 ``/compact`` 入口：跳过阈值与熔断，无条件 force_compact。

        返回 ``(before, after)``；失败让异常向上抛由 TUI 捕获。入口先持 ``_run_lock``，
        保证不与正在进行的 run 并发触发 manage_context。
        """
        async with self._run_lock:
            anchor = self.runtime.usage_anchor
            anchor_len = self.runtime.anchor_msg_len
            cw = self.runtime.context_window
            est = estimate_tokens(anchor, conv.messages(), anchor_len)
            in_ = ManageInput(
                conv=conv,
                provider=self._provider,
                context_window=cw,
                tool_defs=tool_defs,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                usage_anchor=anchor,
                anchor_msg_len=anchor_len,
                estimated_token=est,
                trigger=TriggerKind.MANUAL,
            )
            out = await manage_context(in_)
            return out.before_tokens, out.after_tokens


def new_agent(
    provider: Provider,
    registry: Registry,
    version: str,
    engine: Engine,
    runtime: SessionRuntime | None = None,
) -> Agent:
    """构造 ``Agent``（ch08：增 ``runtime`` 关键字参数）。"""
    return Agent(provider, registry, version, engine, runtime=runtime)


__all__ = [
    "MAX_ITERATIONS",
    "MAX_UNKNOWN_RUN",
    "NOTICE_CANCELLED",
    "NOTICE_MAX_ITER",
    "NOTICE_STREAM_ERR",
    "NOTICE_UNKNOWN_TOOLS",
    "PLAN_REMINDER_INTERVAL",
    "Agent",
    "ApprovalRequest",
    "Event",
    "Mode",
    "Phase",
    "ToolEvent",
    "Usage",
    "new_agent",
]
