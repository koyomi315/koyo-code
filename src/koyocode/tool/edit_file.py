"""edit_file 工具：对唯一匹配的原文片段做替换。

匹配 0 次或多次时返回**可区分**的结构化错误（含匹配数），让模型据此重试（AC4）。
"""

import json
from pathlib import Path
from typing import Any

from koyocode.tool import Result


class EditFileTool:
    """对文件中唯一匹配的 old_string 替换为 new_string。"""

    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "对文件中唯一匹配的 old_string 替换为 new_string；"
            "若匹配 0 处或多于 1 处则返回错误（不修改文件）。"
            "编辑前请先用 read_file 读取目标文件，确认 old_string 唯一。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原文片段（须在文件中唯一匹配）",
                },
                "new_string": {"type": "string", "description": "替换为的新文片段"},
            },
            "required": ["path", "old_string", "new_string"],
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
        old_string = data.get("old_string")
        new_string = data.get("new_string")
        if not isinstance(path, str) or not path:
            return Result(is_error=True, content="缺少参数 path（字符串）")
        if not isinstance(old_string, str):
            return Result(is_error=True, content="缺少参数 old_string（字符串）")
        if not isinstance(new_string, str):
            return Result(is_error=True, content="缺少参数 new_string（字符串）")

        p = Path(path)
        try:
            content = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Result(is_error=True, content=f"文件不存在: {path}")
        except UnicodeDecodeError:
            return Result(is_error=True, content=f"文件非 UTF-8 文本，无法读取: {path}")
        except OSError as e:
            return Result(is_error=True, content=f"读取失败: {e}")

        n = content.count(old_string)
        if n == 0:
            return Result(is_error=True, content="未找到匹配的内容")
        if n > 1:
            return Result(
                is_error=True,
                content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一",
            )

        new_content = content.replace(old_string, new_string, 1)
        try:
            p.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return Result(is_error=True, content=f"写回失败: {e}")

        return Result(content=f"已替换 1 处于 {path}")
