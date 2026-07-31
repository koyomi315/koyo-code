"""系统提示工程化：模块化装配稳定系统提示 + 环境采集 + 补充消息 + 启动 banner。

稳定系统提示由按优先级排列的固定模块拼成（``assemble_system``），可选模块内容
为空时自动跳过；环境信息（``environment``）与补充消息（``reminder``）分属子模块。

旧的单常量 ``SYSTEM_PROMPT`` / ``PLAN_MODE_REMINDER`` 已拆分为模块化装配与
``reminder`` 子模块；``EXECUTE_DIRECTIVE`` 迁至 ``reminder``（本包顶层仍重导出，
import 路径不变）。banner（``LOGO_FONT`` / ``_render_logo`` / ``render_banner``）
以点阵 logo + 功能性头部呈现。
"""

from rich.style import Style
from rich.text import Text

from koyocode.prompt.environment import Environment, gather_environment
from koyocode.prompt.modules import Module, fixed_modules, optional_modules
from koyocode.prompt.reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder

# 品牌颜色与点阵字体：3 列 × 5 行的 1/0 位图，仅含 KOYOCODE 所需字母。
WHALE_BLUE = "#2496ED"  # 鲸鱼蓝（Docker 蓝），logo 像素背景色
LOGO_TEXT = "KOYOCODE"
_PIXEL_ON = "  "  # 两个空格组成近似正方形像素

LOGO_FONT: dict[str, list[str]] = {
    "K": ["101", "110", "100", "110", "101"],
    "O": ["111", "101", "101", "101", "111"],
    "Y": ["101", "101", "010", "010", "010"],
    "C": ["111", "100", "100", "100", "111"],
    "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"],
}

READY_HINT = "Enter 发送 · Alt+Enter 换行 · /plan /do 切换模式 · /exit 退出"


def _render_logo(text: str) -> Text:
    """按 ``LOGO_FONT`` 点阵渲染 ``text``：1 像素着鲸鱼蓝背景，0 留空，字母间留一列。"""
    out = Text()
    blue = Style(bgcolor=WHALE_BLUE)
    for row in range(5):
        for col_idx, char in enumerate(text):
            glyph = LOGO_FONT[char][row]
            for pixel in glyph:
                if pixel == "1":
                    out.append(_PIXEL_ON, style=blue)
                else:
                    out.append(_PIXEL_ON)
            # 字母之间留一列空白（行内最后一个字母不加）
            if col_idx < len(text) - 1:
                out.append(" ")
        out.append("\n")
    return out


def assemble_system(mods: list[Module]) -> str:
    """按 ``priority`` 升序稳定排序、跳过空 ``content``、以 ``\\n\\n`` 连接。

    只用常量内容 -> 跨轮逐字节一致（N1）；空模块不产生连续空行（AC2）。
    """
    ordered = sorted(mods, key=lambda m: m.priority)
    return "\n\n".join(m.content for m in ordered if m.content)


def build_system_prompt() -> str:
    """装配完整稳定系统提示 = ``assemble_system(fixed_modules() + optional_modules())``。"""
    return assemble_system(fixed_modules() + optional_modules())


def render_banner(version: str, cwd: str) -> Text:
    """返回 banner：logo（rich.Text，真彩色背景像素）+ 功能性头部多行文本。

    logo 各行像素以 ``Style(bgcolor=WHALE_BLUE)`` 着色；头部应用名粗体、
    cwd 与按键提示暗淡，整体克制干净。
    """
    out = Text()
    out.append_text(_render_logo(LOGO_TEXT))
    out.append("\n")
    out.append(f"KoyoCode v{version}\n", style="bold")
    out.append(f"{cwd}\n", style="dim")
    out.append(READY_HINT, style="dim")
    return out


__all__ = [
    "Environment",
    "EXECUTE_DIRECTIVE",
    "LOGO_FONT",
    "LOGO_TEXT",
    "Module",
    "READY_HINT",
    "WHALE_BLUE",
    "assemble_system",
    "build_system_prompt",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "render_banner",
    "system_reminder",
]
