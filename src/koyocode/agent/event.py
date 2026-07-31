"""Agent 事件类型（ch08 新增压缩生命周期事件）。

``CompactEvent`` 让 TUI 在 LLM 摘要请求还在跑时立刻显示「压缩中」前缀，
避免用户以为程序卡死（spec F24a / F24b）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CompactPhase(Enum):
    """压缩状态事件阶段。"""

    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactEvent:
    """一次压缩的生命周期事件：BEFORE/AFTER 配对，AFTER 携带 before/after token 与 err。"""

    phase: CompactPhase
    before: int = 0  # BEFORE 阶段无意义置 0
    after: int = 0
    err: Exception | None = None
