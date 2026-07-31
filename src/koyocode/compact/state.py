"""上下文管理状态对象。

- ``SessionContext``：会话生命周期信息（session_id 与落盘目录）。
- ``ContentReplacementState``：工具结果替换决策账本（已见集合 + id->预览映射）。
- ``CompactCircuitBreaker``：自动摘要熔断器（手动 / 紧急路径不读此类）。
- ``RecoveryState`` / ``FileReadRecord``：最近读过文件的并发安全追踪。

三类可变状态均用 ``threading.RLock`` 保护：asyncio 事件循环单线程下本就串行，
锁开销可忽略；在后台线程（如 ``asyncio.to_thread`` 落盘、并发测试）混用场景下
保证「读 -> 决策 -> 写」原子完成，杜绝「已 Seen 但 replacement 未写」中间态。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from koyocode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES

log = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """会话生命周期信息。

    ``session_id`` 进程启动时一次性生成；``spill_dir`` 固定指向
    ``.koyocode/sessions/<session_id>/tool-results/``。
    """

    session_id: str
    spill_dir: str


def _new_session_id() -> str:
    """生成 ``<unix_ts>-<8 字符 hex>`` 形式的会话 id。"""
    try:
        suffix = secrets.token_hex(4)
    except Exception as e:  # noqa: BLE001 - 极少见，降级到 random 兜底
        log.warning("secrets.token_hex 失败，降级使用 random: %s", e)
        import random

        suffix = random.Random(time.time()).randbytes(4).hex()
    return f"{int(time.time())}-{suffix}"


def new_session_context(workspace: str) -> SessionContext:
    """构造会话上下文并按需创建落盘目录（已存在不报错）。"""
    session_id = _new_session_id()
    spill_dir = str(Path(workspace) / ".koyocode" / "sessions" / session_id / "tool-results")
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, spill_dir=spill_dir)


class ContentReplacementState:
    """工具结果替换决策账本。

    ``_seen_ids`` 记录已决策过的 ``tool_use_id``（无论 kept / replaced）；
    ``_replacements`` 只存「决定替换」那一支的预览字符串。同一 id 一旦进入
    ``_seen_ids`` 即不再重新评估，保证 prompt cache 前缀逐字节稳定。对外只暴露
    ``decide_once`` 一个高层方法，由本类型内部统一加锁，消除「读账本 -> 落盘 ->
    写账本」之间的并发翻转窗口。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def decide_once(
        self,
        tool_use_id: str,
        original: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """持锁完成「查账本 -> 决策 -> 写账本」原子操作。

        ``decide`` 回调在持锁状态下被调用，返回 ``(decision, preview)``：
          - ``"kept"``：写 ``_seen_ids``，不写 ``_replacements``；返回 ``original``。
          - ``"replaced"``：写 ``_seen_ids`` + ``_replacements``；返回 ``preview``。
          - ``"skip"``：既不写 ``_seen_ids`` 也不写 ``_replacements``；返回
            ``original``（下一轮重试）。
        id 已 Seen 时直接返回账本存量结果（不再调 ``decide``）：kept 返回原 content、
        replaced 复用 ``_replacements[id]``（不重新构造）。
        """
        with self._lock:
            if tool_use_id in self._seen_ids:
                return self._replacements.get(tool_use_id, original)
            decision, preview = decide()
            if decision == "replaced":
                self._seen_ids.add(tool_use_id)
                self._replacements[tool_use_id] = preview
                return preview
            if decision == "kept":
                self._seen_ids.add(tool_use_id)
                return original
            # skip：不写账本，下一轮重试
            return original

    def is_seen(self, tool_use_id: str) -> bool:
        """该 id 是否已被决策过（只读查询，不写账本）。"""
        with self._lock:
            return tool_use_id in self._seen_ids


class CompactCircuitBreaker:
    """自动摘要熔断器：跟踪连续失败次数，达阈值后停止自动触发。

    手动 / 紧急压缩路径不读此类。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._consecutive_failures = 0

    def record_success(self) -> None:
        """任意一次自动摘要成功即清零连续失败计数。"""
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        """整轮 auto_compact（含 PTL 自重试）未拿到可用摘要时累加一次。"""
        with self._lock:
            self._consecutive_failures += 1

    def tripped(self) -> bool:
        """是否已连续失败达熔断阈值。"""
        with self._lock:
            return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass
class FileReadRecord:
    """单条文件读取记录。``content`` 为不带行号前缀的纯净字节解码字符串。"""

    path: str
    content: str
    timestamp: datetime


class RecoveryState:
    """最近读过文件的追踪状态。Agent 主循环写、compact 摘要时读。

    ``_files`` 键为文件绝对路径，避免相对路径在不同 cwd 下错乱。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        """记录一次文件读取（同路径覆盖，更新时间戳为最近读取时刻）。"""
        abs_path = str(Path(path).resolve())
        with self._lock:
            self._files[abs_path] = FileReadRecord(
                path=abs_path, content=content, timestamp=datetime.now()
            )

    def snapshot(self) -> list[FileReadRecord]:
        """返回按 ``timestamp`` 倒序排序的列表拷贝（修改不影响内部状态）。"""
        with self._lock:
            return sorted(self._files.values(), key=lambda r: r.timestamp, reverse=True)
