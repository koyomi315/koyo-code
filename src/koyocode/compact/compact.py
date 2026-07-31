"""上下文管理编排入口。

``manage_context`` 是 Agent 每轮请求前必调的唯一入口，按 ``trigger`` 分三条路径：
AUTO（先 layer1 再按阈值决定是否 layer2）、MANUAL（跳过 layer1 / 阈值 / 熔断，
直接 force_compact）、EMERGENCY（先强制 layer1 把大工具结果挪走，再 force_compact）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from koyocode.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from koyocode.compact.layer1 import offload_and_snip
from koyocode.compact.layer2 import auto_compact, force_compact
from koyocode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from koyocode.compact.token import estimate_tokens
from koyocode.conversation import Conversation
from koyocode.llm import Provider, ToolDefinition

log = logging.getLogger(__name__)


class TriggerKind(Enum):
    """压缩触发来源。"""

    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    """``manage_context`` 的全部入参。

    ``tool_defs`` 为主循环本轮迭代开头按当前 mode 选好的工具定义列表，恢复段与
    stream 共用此列表（``id`` 相同）；``estimated_token`` 为调用方算好的本轮估算
    token（= anchor + chars/3.5）。
    """

    conv: Conversation
    provider: Provider
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind


@dataclass
class ManageOutput:
    """``manage_context`` 的返回值：压缩前后估算 token。"""

    before_tokens: int
    after_tokens: int


async def manage_context(in_: ManageInput) -> ManageOutput:
    """按 ``trigger`` 编排两层压缩，把结果写回 ``Conversation``。

    MANUAL：跳过 layer1 / 阈值 / 熔断，直接 force_compact。
    EMERGENCY：先强制 layer1 把大工具结果挪走，再 force_compact（避免摘要请求
      自身撞 PTL）。
    AUTO：先 layer1（无论是否 layer2 都写回，否则替换不作用于下次 stream），
      用 layer1 之后的消息重估 token；若 context_window 过小（≤ 33000）跳过
      layer2 避免死循环；若重估 token < 阈值或已熔断则仅 layer1；否则 auto_compact。
    """
    if in_.trigger == TriggerKind.MANUAL:
        new_msgs, before_tok, after_tok = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before_tokens=before_tok, after_tokens=after_tok)

    if in_.trigger == TriggerKind.EMERGENCY:
        layer1_out = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
        in_.conv.replace_history(layer1_out)
        new_msgs, before_tok, after_tok = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before_tokens=before_tok, after_tokens=after_tok)

    # AUTO 路径
    layer1_out = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
    in_.conv.replace_history(layer1_out)
    est_tokens = estimate_tokens(in_.usage_anchor, layer1_out, in_.anchor_msg_len)

    if in_.context_window <= SUMMARY_RESERVE + AUTO_SAFETY_MARGIN:
        log.warning(
            "context_window=%d 过小，跳过自动 layer2 避免阈值非正死循环", in_.context_window
        )
        return ManageOutput(before_tokens=in_.estimated_token, after_tokens=est_tokens)

    threshold = in_.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
    if est_tokens < threshold or in_.auto_tracking.tripped():
        return ManageOutput(before_tokens=in_.estimated_token, after_tokens=est_tokens)

    new_msgs, before_tok, after_tok = await auto_compact(in_)
    in_.conv.replace_history(new_msgs)
    return ManageOutput(before_tokens=before_tok, after_tokens=after_tok)
