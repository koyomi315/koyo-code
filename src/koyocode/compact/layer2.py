"""第 2 层 LLM 全量摘要 + 恢复 + 近期原文拼接。

包含摘要请求（``summarize_once``）、摘要请求自身 PTL 的丢消息组重试
（``ptl_retry``）、摘要 + 恢复 + 近期原文拼装（``run_summary``）、自动 / 手动
两条入口（``auto_compact`` / ``force_compact``），以及近期原文边界推算
（``pick_recent_tail``）与消息分组（``group_by_user_turn``）。

摘要请求不传 tools、不更新主对话 usage 锚点；PTL 由 ``isinstance(err,
PromptTooLongError)`` 识别后切到 ``ptl_retry``。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from koyocode.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
)
from koyocode.compact.recovery import build_recovery_attachment
from koyocode.compact.summary_prompt import build_summary_prompt, extract_summary
from koyocode.compact.token import estimate_tokens, message_chars
from koyocode.llm import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Message,
    PromptTooLongError,
    Request,
    System,
)

if TYPE_CHECKING:
    from koyocode.compact.compact import ManageInput

log = logging.getLogger(__name__)

# 摘要 + 恢复合并后接近期原文时，避免 user/user 连续的衔接占位
_CONTEXT_LOADED_NOTICE = "（已加载上下文摘要与恢复信息。请继续。）"


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    """从尾部倒序累加，累计 token ≥ ``RECENT_KEEP_TOKENS`` 且条数 ≥
    ``RECENT_KEEP_MESSAGES``（两个下界都满足）后停止，再做 tool_use/tool_result
    配对修正：起点为 tool 时前推到带 tool_uses 的 assistant。
    """
    if not msgs:
        return []
    start_idx = len(msgs)
    acc_chars = 0
    acc_count = 0
    for i in range(len(msgs) - 1, -1, -1):
        acc_chars += message_chars([msgs[i]])
        acc_count += 1
        start_idx = i
        if (
            math.ceil(acc_chars / ESTIMATE_CHARS_PER_TOKEN) >= RECENT_KEEP_TOKENS
            and acc_count >= RECENT_KEEP_MESSAGES
        ):
            break
    while start_idx > 0 and msgs[start_idx].role == "tool":
        start_idx -= 1
        if msgs[start_idx].role == "assistant" and msgs[start_idx].tool_uses:
            break
    return list(msgs[start_idx:])


def _join_after_summary(summary_and_recovery: Message, recent: list[Message]) -> list[Message]:
    """把摘要+恢复消息与近期原文拼成合法交替序列（避免 user/user 连续）。

    summary_and_recovery 固定 role=user。recent 首条为 user 时插入 assistant
    衔接占位；首条为 tool 时防御性前移到非 tool（pick_recent_tail 应已修正）。
    """
    if not recent:
        return [summary_and_recovery]
    first = recent[0]
    placeholder = Message(role=ROLE_ASSISTANT, content=_CONTEXT_LOADED_NOTICE)
    if first.role == "user":
        return [summary_and_recovery, placeholder, *recent]
    if first.role == "tool":
        idx = 0
        while idx < len(recent) and recent[idx].role == "tool":
            idx += 1
        recent = recent[idx:]
        if not recent:
            return [summary_and_recovery]
        if recent[0].role == "user":
            return [summary_and_recovery, placeholder, *recent]
        return [summary_and_recovery, *recent]
    return [summary_and_recovery, *recent]


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    """按「用户提交 -> 一组 assistant/tool 往返」分组：每遇 user 开新组。"""
    groups: list[list[Message]] = []
    for msg in msgs:
        if msg.role == "user" or not groups:
            groups.append([msg])
        else:
            groups[-1].append(msg)
    return groups


async def summarize_once(in_: ManageInput, msgs: list[Message]) -> str:
    """发一次摘要请求（不传 tools），返回 ``<summary>`` 正文。

    ``ev.err`` 非 None 时立即抛出（PTL 由调用方用 ``isinstance`` 识别）。
    摘要请求的 usage 捕获但不回写主对话锚点。
    """
    req = Request(
        messages=build_summary_prompt(msgs),
        tools=[],
        system=System(),
        reminder="",
    )
    text_buf: list[str] = []
    async for ev in in_.provider.stream(req):
        if ev.err is not None:
            raise ev.err
        if ev.text:
            text_buf.append(ev.text)
    return extract_summary("".join(text_buf))


async def ptl_retry(in_: ManageInput, msgs: list[Message], first_err: Exception) -> str:
    """摘要请求自身撞 PTL 的丢消息组重试（F27）。

    前 ``PTL_RETRY_LIMIT`` 次每次丢最旧 1 组；之后每次丢
    ``ceil(剩余组数 × PTL_DROP_PERCENTAGE)``（至少 1 组）。丢光仍失败则抛最近一次
    异常，不发送 messages 为空的摘要请求。中间任何非 PTL 异常立即上抛。
    """
    groups = group_by_user_turn(msgs)
    err = first_err
    direct = 0
    while True:
        if direct < PTL_RETRY_LIMIT:
            drop = 1
            direct += 1
        else:
            drop = max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
        if drop >= len(groups):
            raise err
        groups = groups[drop:]
        flattened = [m for g in groups for m in g]
        try:
            return await summarize_once(in_, flattened)
        except PromptTooLongError as e:
            err = e
            continue


async def run_summary(in_: ManageInput) -> list[Message]:
    """两条路径共同核心：摘要请求 + 恢复三段 + 近期原文拼接。

    入口先拍一次 ``recovery_snapshot``，整个生命周期只用这一份，避免渲染期间
    ``record_file`` 写入造成漂移。PTL 走 ``ptl_retry``；其内部非 PTL 异常 / 丢光
    仍失败的异常直接上抛给调用方。
    """
    old_msgs = in_.conv.messages()
    recovery_snapshot = in_.recovery.snapshot()
    try:
        summary_text = await summarize_once(in_, old_msgs)
    except PromptTooLongError as e:
        summary_text = await ptl_retry(in_, old_msgs, e)

    recovery_text = build_recovery_attachment(recovery_snapshot, in_.tool_defs)
    combined = f"## 历史会话摘要\n{summary_text}\n\n{recovery_text}"
    summary_and_recovery = Message(role=ROLE_USER, content=combined)
    recent_tail = pick_recent_tail(old_msgs)
    return _join_after_summary(summary_and_recovery, recent_tail)


async def auto_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    """自动路径：整轮（含 PTL 自重试）失败累加熔断计数并抛出；成功清零。

    返回 ``(new_msgs, before_tok, after_tok)``，不写回 conversation（由
    ``manage_context`` 负责）。
    """
    before_tok = in_.estimated_token
    try:
        new_msgs = await run_summary(in_)
    except Exception:
        in_.auto_tracking.record_failure()
        raise
    in_.auto_tracking.record_success()
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok


async def force_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    """手动 / 紧急路径：跳过熔断器，失败也不计入熔断。"""
    before_tok = in_.estimated_token
    new_msgs = await run_summary(in_)
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok
