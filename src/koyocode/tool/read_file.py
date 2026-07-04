"""read_file 工具：读取本地文件文本内容（带行号）。

文件不存在 / 是目录 / 不可读 / 非 UTF-8 均以结构化错误返回，不抛异常。
大文件按行数与字节数上限截断并标注 ``[truncated]``（N5/AC2）。
"""

import json
from pathlib import Path
from typing import Any

from koyocode.tool import Result, _truncate

_MAX_LINES = 2000
_MAX_BYTES = 256 * 1024


class ReadFileTool:
    """读取文件文本，带行号返回。"""

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取本地文件的文本内容，按行带行号返回，便于定位引用。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数 JSON 解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content=f"参数应为 JSON 对象，得到 {type(data).__name__}")

        path = data.get("path")
        if not isinstance(path, str) or not path:
            return Result(is_error=True, content="缺少参数 path（字符串）")

        p = Path(path)
        if p.is_dir():
            return Result(is_error=True, content=f"目标是目录，不是文件: {path}")
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Result(is_error=True, content=f"文件不存在: {path}")
        except UnicodeDecodeError:
            return Result(is_error=True, content=f"文件非 UTF-8 文本，无法读取: {path}")
        except OSError as e:
            return Result(is_error=True, content=f"读取失败: {e}")

        lines = text.splitlines()
        numbered = "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(lines, 1))
        return Result(content=_truncate(numbered, _MAX_LINES, _MAX_BYTES))
