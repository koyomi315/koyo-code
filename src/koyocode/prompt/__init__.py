"""系统提示工程化：模块化装配稳定系统提示 + 环境采集 + 补充消息 + 启动 banner。

稳定系统提示由按优先级排列的固定模块拼成（``assemble_system``），可选模块内容
为空时自动跳过；环境信息（``environment``）与补充消息（``reminder``）分属子模块。

旧的单常量 ``SYSTEM_PROMPT`` / ``PLAN_MODE_REMINDER`` 已拆分为模块化装配与
``reminder`` 子模块；``EXECUTE_DIRECTIVE`` 迁至 ``reminder``（本包顶层仍重导出，
import 路径不变）。banner（``CAT_BANNER`` / ``READY_HINT`` / ``render_banner``）保留。
"""

from koyocode.prompt.environment import Environment, gather_environment
from koyocode.prompt.modules import Module, fixed_modules, optional_modules
from koyocode.prompt.reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder

CAT_BANNER = r"""  /\_/\
 ( o.o )
  > ^ <"""

READY_HINT = "● 就绪：Enter 发送 · Alt+Enter 换行 · /plan /do 切换模式 · /exit 或 Ctrl+C 退出"


def assemble_system(mods: list[Module]) -> str:
    """按 ``priority`` 升序稳定排序、跳过空 ``content``、以 ``\\n\\n`` 连接。

    只用常量内容 -> 跨轮逐字节一致（N1）；空模块不产生连续空行（AC2）。
    """
    ordered = sorted(mods, key=lambda m: m.priority)
    return "\n\n".join(m.content for m in ordered if m.content)


def build_system_prompt() -> str:
    """装配完整稳定系统提示 = ``assemble_system(fixed_modules() + optional_modules())``。"""
    return assemble_system(fixed_modules() + optional_modules())


def render_banner(version: str, cwd: str) -> str:
    """拼出启动横幅：猫 + 应用名与版本 + 工作目录 + 就绪提示行。"""
    lines = [
        CAT_BANNER,
        f"  koyoCode v{version}",
        f"  cwd: {cwd}",
        "",
        READY_HINT,
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "CAT_BANNER",
    "Environment",
    "EXECUTE_DIRECTIVE",
    "Module",
    "READY_HINT",
    "assemble_system",
    "build_system_prompt",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "render_banner",
    "system_reminder",
]
