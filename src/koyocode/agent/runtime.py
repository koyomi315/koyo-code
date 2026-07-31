"""会话级长生命周期状态容器（ch08 上下文管理）。

TUI Model 跨 run 持有 ``SessionRuntime``，避免每轮重新构造 Agent 时把压缩状态
（替换决策账本、文件追踪、熔断计数、usage 锚点）重置--决策冻结与熔断器依赖
跨轮存活。Agent 通过关键字参数 ``runtime`` 注入同一份实例。
"""

from __future__ import annotations

from dataclasses import dataclass

from koyocode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    new_session_context,
)


@dataclass
class SessionRuntime:
    """跨 run 持有的压缩状态 + usage 锚点。

    ``usage_anchor`` 只由主对话路径 stream 真实 usage 维护（替换，非累加）；
    摘要请求结束后不更新这两个字段。``anchor_msg_len`` 为 anchor 被记录时
    ``conv.length()``，下次估算只算这之后追加的字符增量。
    """

    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    context_window: int = 200000
    usage_anchor: int = 0
    anchor_msg_len: int = 0


def new_session_runtime(workspace: str = ".", context_window: int = 200000) -> SessionRuntime:
    """构造默认 SessionRuntime（cli / smoke 启动期调用）。"""
    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(workspace),
        context_window=context_window,
    )
