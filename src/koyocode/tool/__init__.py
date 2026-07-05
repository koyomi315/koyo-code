"""工具系统：统一工具抽象、执行结果、注册中心。

对外暴露 ``Tool`` Protocol、``Result`` 值类型、``Registry`` 注册中心、
``new_default_registry`` 工厂与 ``DEFAULT_TIMEOUT`` 常量。

设计要点：
- 工具 ``execute`` 永远以 ``Result`` 值类型返回，从不抛 Python 异常给上层
  （F9/N4）。``Registry.execute`` 再兜一层 ``asyncio.wait_for`` 超时与异常捕获。
- 零外部依赖、不感知 LLM 协议；仅 ``definitions()`` 借用 ``llm.ToolDefinition``
  作为导出载体。
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from koyocode.llm import ToolDefinition

DEFAULT_TIMEOUT: float = 30.0
"""单个工具执行的默认超时秒数（N1，不可配）。"""


@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""

    content: str
    is_error: bool = False


@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""

    def name(self) -> str:
        """模型看到的工具名，如 "read_file"。"""
        ...

    def description(self) -> str:
        """给模型的用途说明。"""
        ...

    def parameters(self) -> dict[str, Any]:
        """手写 JSON Schema（type/properties/required/description）。"""
        ...

    async def execute(self, args: str) -> Result:
        """执行工具；``args`` 为 raw JSON 字符串，超时由外部 ``asyncio.wait_for`` 控制。"""
        ...


def _truncate(s: str, max_lines: int, max_chars: int) -> str:
    """按行数与字符数上限截断；超出尾部追加 ``\\n[truncated]`` 标注。"""
    truncated = False
    lines = s.split("\n")
    if len(lines) > max_lines:
        s = "\n".join(lines[:max_lines])
        truncated = True
    if len(s) > max_chars:
        s = s[:max_chars]
        truncated = True
    if truncated:
        s = s + "\n[truncated]"
    return s


class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        """登记工具；重复名抛 ``ValueError``。"""
        name = t.name()
        if name in self._tools:
            raise ValueError(f"工具已注册: {name}")
        self._tools[name] = t
        self._order.append(name)

    def get(self, name: str) -> Tool | None:
        """按名查找；未命中返回 ``None``。"""
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        """按注册顺序导出工具定义（F3/AC1）。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
        ]

    async def execute(self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        """按名执行工具，受超时保护；任何失败包成 ``Result(is_error=True)``。

        ``CancelledError`` 不被捕获（透传以支持 task 取消）。
        """
        tool = self.get(name)
        if tool is None:
            return Result(is_error=True, content=f"未知工具: {name}")
        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(is_error=True, content=f"工具 {name} 执行超时（{timeout}s）")
        except Exception as e:  # noqa: BLE001 — 任意运行时错误均转为结构化错误
            return Result(is_error=True, content=f"工具 {name} 异常: {e}")


def new_default_registry() -> Registry:
    """构造并注册 6 个核心工具，返回 ``Registry``。

    各工具实现见同包下 ``read_file`` / ``write_file`` / ``edit_file`` / ``bash`` /
    ``glob_tool`` / ``grep_tool`` 模块。
    """
    # 延迟导入避免循环依赖（工具模块反向依赖本模块的 Tool/Result/_truncate）。
    from koyocode.tool.bash import BashTool
    from koyocode.tool.edit_file import EditFileTool
    from koyocode.tool.glob_tool import GlobTool
    from koyocode.tool.grep_tool import GrepTool
    from koyocode.tool.read_file import ReadFileTool
    from koyocode.tool.write_file import WriteFileTool

    registry = Registry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    return registry


__all__ = [
    "DEFAULT_TIMEOUT",
    "Registry",
    "Result",
    "Tool",
    "new_default_registry",
]
