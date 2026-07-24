"""运行环境信息采集与渲染（F2/AC3）。

采集工作目录、平台、当前日期、git 状态、应用版本、当前模型，渲染为系统提示的
独立第二段（与可缓存的稳定模块分属不同内容块，F3）。采集快速且有界：git 外调
带 2s 超时，任一项取不到时降级留空（N4）；不读取任何环境变量（N5）。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from dataclasses import dataclass

_GIT_TIMEOUT = 2.0


@dataclass
class Environment:
    """运行环境信息（不缓存，随采集时刻变化）。"""

    working_dir: str
    platform: str
    date: str
    git_status: str
    version: str
    model: str

    def render(self) -> str:
        """渲染为「环境信息」段：逐行 ``Key: Value``，空值项省略。"""
        items: list[tuple[str, str]] = [
            ("Working directory", self.working_dir),
            ("Platform", self.platform),
            ("Date", self.date),
            ("Git status", self.git_status),
            ("Version", self.version),
            ("Model", self.model),
        ]
        lines = ["## Environment"]
        lines.extend(f"- {key}: {value}" for key, value in items if value)
        return "\n".join(lines)


def _gather_git_status() -> str:
    """采集 git 状态摘要；非 git 目录 / git 不可用 / 超时均降级为空串（N4）。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not changed:
        return "clean"
    return f"{len(changed)} file(s) changed"


def gather_environment(version: str, model: str) -> Environment:
    """采集运行环境信息。

    ``version`` 与 ``model`` 由调用方透传；``working_dir`` 捕获 ``OSError`` 留空；
    不读取任何环境变量（N5）。
    """
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""
    return Environment(
        working_dir=working_dir,
        platform=sys.platform,
        date=datetime.date.today().isoformat(),
        git_status=_gather_git_status(),
        version=version,
        model=model,
    )


__all__ = ["Environment", "gather_environment"]
