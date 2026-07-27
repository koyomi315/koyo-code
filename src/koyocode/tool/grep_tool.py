"""grep 工具：按正则在文件内容中检索，返回命中位置（file:line:content）。

正则非法返回 ``is_error``；无命中返回空说明（非 ``is_error``）；
命中数上限 100，超出标注；超长行标注未完整搜索；每文件让出 event loop（N2）。
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from koyocode.tool import ToolResult

_MAX_HITS = 100
_MAX_LINE = 1024 * 1024  # 单行超过 ~1MB 视为过长，跳过完整搜索


class GrepTool:
    """在文件内容中搜索正则。"""

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "按 Python 正则在文件内容中检索，返回命中位置（file:line:content）。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "path": {"type": "string", "description": "搜索根目录，默认当前工作目录"},
                "glob": {
                    "type": "string",
                    "description": "文件名过滤模式，如 *.py；默认搜索所有文件",
                },
            },
            "required": ["pattern"],
        }

    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> ToolResult:
        try:
            data = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            return ToolResult(is_error=True, content=f"参数 JSON 解析失败: {e}")
        if not isinstance(data, dict):
            return ToolResult(
                is_error=True, content=f"参数应为 JSON 对象，得到 {type(data).__name__}"
            )

        pattern = data.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult(is_error=True, content="缺少参数 pattern（字符串）")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult(is_error=True, content=f"正则非法: {e}")

        root = Path(data.get("path") or ".")
        glob_pat = data.get("glob") or "*"

        hits: list[str] = []
        truncated = False
        count = 0
        try:
            for file in root.rglob(glob_pat):
                if not file.is_file():
                    continue
                count += 1
                try:
                    with file.open(encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if len(line) > _MAX_LINE:
                                msg = f"{file}:{lineno}:[该行过长，未完整搜索]"
                                if len(hits) < _MAX_HITS:
                                    hits.append(msg)
                                else:
                                    truncated = True
                                continue
                            if rx.search(line):
                                if len(hits) < _MAX_HITS:
                                    hits.append(f"{file}:{lineno}:{line.rstrip()}")
                                else:
                                    truncated = True
                                    break
                except OSError:
                    continue
                if count % 50 == 0:
                    await asyncio.sleep(0)
                if truncated:
                    break
        except OSError as e:
            return ToolResult(is_error=True, content=f"搜索失败: {e}")

        if not hits:
            return ToolResult(content="无命中")
        text = "\n".join(hits)
        if truncated:
            text += "\n[truncated]"
        return ToolResult(content=text)
