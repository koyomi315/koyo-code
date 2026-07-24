"""权限系统：前四层判定（黑名单/沙箱/规则/模式兜底）与配置加载。

对外暴露：

- ``Mode``：四档权限模式（``DEFAULT``/``ACCEPT_EDITS``/``PLAN``/``BYPASS``），
  ``parse_mode`` 大小写不敏感解析。
- ``Decision``：单次裁决（``ALLOW``/``DENY``/``ASK``）。
- ``Category``：工具类别（``READ``/``WRITE``/``EXEC``）。
- ``Outcome``：人在回路三选一结果（``DENY_ONCE``/``ALLOW_ONCE``/``ALLOW_FOREVER``）。
- ``Engine`` / ``new_engine`` / ``ApprovalError``：引擎与构造、永久规则写入异常。
- ``persist_local_allow``：便捷重导出（同 ``Engine.persist_local_allow`` 方法）。

前四层由 ``Engine.check`` 承载（黑名单→沙箱→规则→模式兜底，短路）；第五层人在回路由
agent 在 Ask 后编排驱动（见 ``agent`` 包）。模块仅依赖 ``llm``（``ToolCall``）与标准库 +
``pyyaml``，不感知 provider 协议。
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "ApprovalError",
    "Category",
    "Decision",
    "Engine",
    "Mode",
    "Outcome",
    "new_engine",
    "parse_mode",
    "persist_local_allow",
]


class Mode(IntEnum):
    """权限模式四档（统一一个模式轴）。

    - ``DEFAULT``：只读 Allow / 文件写 Ask / 命令执行 Ask。
    - ``ACCEPT_EDITS``：文件写 Allow / 命令执行 Ask。
    - ``PLAN``：仅只读工具可见（沿用 ch04）；矩阵同 default 作防御兜底。
    - ``BYPASS``：全 Allow（黑名单/沙箱仍拦）。
    """

    DEFAULT = 0
    ACCEPT_EDITS = 1
    PLAN = 2
    BYPASS = 3

    def __str__(self) -> str:  # noqa: D401 - 见 docstring
        # 配置文件 / 状态栏用的规范名。
        return {
            Mode.DEFAULT: "default",
            Mode.ACCEPT_EDITS: "acceptEdits",
            Mode.PLAN: "plan",
            Mode.BYPASS: "bypassPermissions",
        }[self]


# 规范名 -> Mode 的反向映射（大小写不敏感，见 parse_mode）。
_MODE_NAMES: dict[str, Mode] = {
    "default": Mode.DEFAULT,
    "acceptedits": Mode.ACCEPT_EDITS,
    "plan": Mode.PLAN,
    "bypasspermissions": Mode.BYPASS,
}


def parse_mode(s: str) -> tuple[Mode, bool]:
    """大小写不敏感识别四档名；未知返回 ``(_, False)``。

    返回 ``(Mode, ok)``：``ok=False`` 表示未识别（调用方按默认 ``Mode.DEFAULT`` 处理）。
    """
    if not isinstance(s, str):
        return Mode.DEFAULT, False
    mode = _MODE_NAMES.get(s.strip().lower())
    if mode is None:
        return Mode.DEFAULT, False
    return mode, True


class Decision(IntEnum):
    """单次权限裁决。"""

    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    """工具类别（影响沙箱/模式矩阵）。"""

    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    """人在回路三选一结果（agent 在 Ask 后编排应用）。"""

    DENY_ONCE = 0
    ALLOW_ONCE = 1
    ALLOW_FOREVER = 2


class ApprovalError(Exception):
    """永久规则写入相关异常（调用方仅记日志、不阻断执行）。"""


def __getattr__(name: str):  # noqa: ANN001 - PEP 562 延迟导入：engine/persist 在后续任务创建
    if name == "Engine":
        from koyocode.permission.engine import Engine

        return Engine
    if name == "new_engine":
        from koyocode.permission.engine import new_engine

        return new_engine
    if name == "persist_local_allow":
        from koyocode.permission.persist import persist_local_allow

        return persist_local_allow
    raise AttributeError(f"module 'koyocode.permission' has no attribute {name!r}")