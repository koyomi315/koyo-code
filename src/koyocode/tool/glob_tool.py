"""glob 工具：按 glob 模式列出匹配的文件路径。

无匹配返回空说明（非 ``is_error``）；命中数上限 100，超出尾部标注 ``[truncated]``。
遍历期间周期性 ``await asyncio.sleep(0)`` 让出 event loop（N2）。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from koyocode.tool import Result

_MAX_MATCHES = 100


class GlobTool:
    """按 glob 模式查找文件。"""

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式（如 **/*.py）列出匹配的文件路径。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
                "path": {"type": "string", "description": "搜索根目录，默认当前工作目录"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数 JSON 解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content=f"参数应为 JSON 对象，得到 {type(data).__name__}")

        pattern = data.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return Result(is_error=True, content="缺少参数 pattern（字符串）")
        root = Path(data.get("path") or ".")

        matches: list[str] = []
        truncated = False
        count = 0
        try:
            for p in root.glob(pattern):
                count += 1
                if p.is_file():
                    if len(matches) < _MAX_MATCHES:
                        matches.append(str(p))
                    else:
                        truncated = True
                        break
                if count % 100 == 0:
                    await asyncio.sleep(0)
        except OSError as e:
            return Result(is_error=True, content=f"glob 失败: {e}")

        if not matches:
            return Result(content="无匹配")
        text = "\n".join(sorted(matches))
        if truncated:
            text += "\n[truncated]"
        return Result(content=text)
