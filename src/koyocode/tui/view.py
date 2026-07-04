"""TUI 渲染辅助：用户块、助手块、错误块、状态栏、流式动态区的纯函数构造。

这些函数不依赖 App 状态，便于独立测试与复用。完成块（用户输入 / 助手回复 /
错误）均为 Rich renderable，由调用方写入 ``RichLog`` 追加到滚动历史。
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text


def user_block(text: str) -> Text:
    """用户输入块：粗体 + 圆点前缀（无 You 文字标签）。"""
    return Text(f"● {text}", style="bold")


def assistant_block(reply: str, elapsed_s: int | None = None) -> Group:
    """助手回复块：圆点 + Markdown 渲染 + 可选总耗时。"""
    parts: list[RenderableType] = [Text("●", style="cyan"), Markdown(reply)]
    if elapsed_s is not None:
        parts.append(Text(f"  ✓ {elapsed_s}s", style="dim"))
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


def render_statusbar(name: str, model: str) -> Table:
    """状态栏：左 provider 名、右 model 名，两端对齐铺满宽度。"""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(Text(f"● {name}", style="bold"), Text(model, style="dim"))
    return grid
