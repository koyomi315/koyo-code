"""TUI 渲染辅助：用户块、助手块、错误块、状态栏、流式动态区的纯函数构造。

这些函数不依赖 App 状态，便于独立测试与复用。完成块（用户输入 / 助手回复 /
错误）均为 Rich renderable，由调用方写入 ``RichLog`` 追加到滚动历史。
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

_TOOL_RESULT_MAX_LINES = 8


def user_block(text: str) -> Text:
    """用户输入块：粗体 + 圆点前缀（无 You 文字标签）。"""
    return Text(f"● {text}", style="bold")


def assistant_block(reply: str, elapsed_s: int | None = None) -> Group:
    """助手回复块：圆点 + Markdown 渲染 + 可选总耗时。"""
    parts: list[RenderableType] = [Text("●", style="cyan"), Markdown(reply)]
    if elapsed_s is not None:
        parts.append(Text(f"  √ {elapsed_s}s", style="dim"))
    return Group(*parts)


def error_block(err: Exception) -> Text:
    """错误块：红色，与正文可区分。"""
    return Text(f"● {err}", style="bold red")


def streaming_view(cur_reply: str, elapsed_s: int) -> Group:
    """流式动态区：正在输出的正文 + Imagining 计时（首个增量前仅显示计时）。"""
    parts: list[RenderableType] = []
    if cur_reply:
        parts.append(Text(cur_reply))
    parts.append(Text(f"Imagining… ({elapsed_s}s)", style="dim italic"))
    return Group(*parts)


def streaming_tool_view(name: str, args: str, elapsed_s: int) -> Text:
    """流式动态区（工具执行中）：工具行 + Running 计时。"""
    return Text(f"● {name}({args}) Running… ({elapsed_s}s)", style="bold cyan")


def tool_line(name: str, args: str) -> Text:
    """已完成工具行：圆点 + name(args) 加粗（F8/AC11）。"""
    return Text("● ", style="bold cyan") + Text(f"{name}({args})", style="bold")


def tool_result_summary(result: str, is_error: bool) -> Padding:
    """工具结果摘要：└ 前缀 + 缩进，UI 截断约 8 行（AC13/N5）。"""
    lines = result.splitlines()
    if len(lines) > _TOOL_RESULT_MAX_LINES:
        body = "\n".join(lines[:_TOOL_RESULT_MAX_LINES]) + "\n[…]"
    else:
        body = result
    style = "bold red" if is_error else "dim"
    return Padding(Text(f"└ {body}", style=style), (0, 0, 0, 2))


def render_statusbar(name: str, model: str) -> Table:
    """状态栏：左 provider 名、右 model 名，两端对齐铺满宽度。"""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(Text(f"● {name}", style="bold"), Text(model, style="dim"))
    return grid
