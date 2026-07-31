"""上下文管理子包（ch08）。

两层压缩 + 压缩后恢复 + 手动 / 紧急入口。对外只暴露窄接口 ``manage_context``，
其余状态对象供 Agent / TUI 注入。子模块按职责拆分：const / state / token /
layer1 / layer2 / summary_prompt / recovery / compact。
"""

from koyocode.compact.compact import ManageInput, ManageOutput, TriggerKind, manage_context
from koyocode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    new_session_context,
)
from koyocode.compact.token import estimate_tokens, message_chars, usage_anchor

__all__ = [
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "FileReadRecord",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "TriggerKind",
    "estimate_tokens",
    "manage_context",
    "message_chars",
    "new_session_context",
    "usage_anchor",
]
