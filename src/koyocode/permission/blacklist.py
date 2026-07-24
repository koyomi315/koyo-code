"""危险命令黑名单（N1）：内置正则集，``hits_blacklist`` 纯不可配置。

设计约束：

- **启发式、非完备**：不可能穷尽所有危险命令，本文档显式声明非完备；防御纵深由
  沙箱、规则、人在回路共同补足。
- **不可配置放开**：模块级编译好的常量列表，无加载入口；任何配置 / 模式（含 BYPASS）
  都碰不到它——``check`` 在最前层无条件短路（命中即 Deny）。
- 仅对 **EXEC 类（bash 命令串）** 生效：由 ``Engine.check`` 在 ``cat == Category.EXEC``
  分支调用。
"""

from __future__ import annotations

import re

__all__ = ["hits_blacklist"]


# 危险命令模式集（``re.search`` 命中即判定危险；不可配置，N1）。
#
# 约束说明：覆盖典型灾难性命令——
# - ``rm -rf / | ~ | $HOME | /*``（删除根/家目录/全盘通配）
# - ``dd of=/dev/``（写裸设备）
# - fork bomb（``:(){ :|:& };:`` 及其变体）
# - ``mkfs.``（重建文件系统）
# - ``> /dev/sd|nvme|disk``（覆写块设备）
# - ``chmod -R 777 /``（递归放开根目录权限）
# - ``:(){...&};:`` 形式经多个空白变体同样命中。
_BLACKLIST: list[re.Pattern[str]] = [
    # rm 带 -r/-f 递归强制删除，目标是根 / 家目录 / 全盘通配
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|/\*|\$HOME/\*)"),
    # dd 写裸设备
    re.compile(r"\bdd\b.*\bof=/dev/"),
    # fork bomb：:( ){ :|:& };: 及空白变体
    re.compile(r":\s*\(\s*\)\s*\{.*\}\s*;\s*:"),
    # mkfs 重建文件系统
    re.compile(r"\bmkfs\."),
    # 重定向覆写块设备
    re.compile(r">\s*/dev/(sd|hd|nvme|disk)"),
    # chmod -R 0?777 / 递归放开根权限
    re.compile(r"\bchmod\s+-R\s+0?777\s+/"),
    # shutdown / reboot / halt / poweroff / init 0 等关机重启
    re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b"),
]


def hits_blacklist(command: str) -> bool:
    """命中任一危险模式返回 ``True``；启发式、非完备、不可配置放开（N1）。"""
    return any(p.search(command) for p in _BLACKLIST)
