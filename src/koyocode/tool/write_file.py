"""write_file 工具：写入（覆盖）文件，父目录不存在时自动创建。

任何 ``OSError`` 以结构化错误返回，不抛异常（AC3）。
"""

import json
from pathlib import Path
from typing import Any

from koyocode.tool import Result


class WriteFileTool:
    """写入文件内容（覆盖）。"""

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "将文本内容写入文件（覆盖已有内容）；父目录不存在时自动创建。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的文本内容"},
            },
            "required": ["path", "content"],
        }

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数 JSON 解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content=f"参数应为 JSON 对象，得到 {type(data).__name__}")

        path = data.get("path")
        content = data.get("content")
        if not isinstance(path, str) or not path:
            return Result(is_error=True, content="缺少参数 path（字符串）")
        if not isinstance(content, str):
            return Result(is_error=True, content="缺少参数 content（字符串）")

        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return Result(is_error=True, content=f"写入失败: {e}")

        return Result(content=f"已写入 {path}（{len(content.encode('utf-8'))} 字节）")
