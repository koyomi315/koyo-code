"""压缩后恢复三段：最近读过的文件 + 当前可用工具 + 边界提示。

摘要完成后在摘要之后、近期原文之前追加这段恢复内容，把摘要无法精确还原的关键
事实补回来。``build_recovery_attachment`` 是纯函数，入参 snapshot 由调用方一次性
拍好，避免渲染期间 ``RecoveryState`` 写入造成漂移。返回纯文本 ``str``，由
``run_summary`` 与摘要文本拼到同一条 user 消息上。
"""

from __future__ import annotations

import json

from koyocode.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
)
from koyocode.compact.state import FileReadRecord
from koyocode.llm import ToolDefinition

BOUNDARY_NOTICE = """\
需要文件原文、错误原文、用户原话时，请使用文件读取工具重新读取对应路径，\
不要依据摘要内容做猜测。摘要可能丢失或简化细节，任何需要精确内容的判断都应回到原始文件。"""


def render_file_block(rec: FileReadRecord) -> str:
    """渲染单个文件快照：路径 / 读取时间戳 / 内容片段（超限保留头部、截掉尾部）。"""
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    content = rec.content
    truncated = len(content) > char_limit
    if truncated:
        content = content[:char_limit]
    parts = [
        f"### {rec.path}",
        f"[read at] {rec.timestamp.isoformat()}",
        content,
    ]
    if truncated:
        parts.append("(content truncated)")
    return "\n".join(parts) + "\n"


def render_tools_block(defs: list[ToolDefinition]) -> str:
    """渲染工具列表：每个工具一行名称 + 用途，再缩进一行展示 input_schema 紧凑 JSON。"""
    lines: list[str] = []
    for d in defs:
        schema = json.dumps(d.input_schema, separators=(",", ":"), ensure_ascii=False)
        lines.append(f"- {d.name}: {d.description}")
        lines.append(f"  schema: {schema}")
    return "\n".join(lines)


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    """拼装恢复三段文本（最近读过的文件 / 当前可用工具 / 边界提示）。

    ``snapshot`` 须已按时间戳倒序；取前 ``RECOVERY_FILE_LIMIT`` 个。返回的文本与
    下一次 LLM 请求的 ``tools`` 参数一致性由调用方保证（同一份 ``tool_defs`` 引用）。
    """
    files = snapshot[:RECOVERY_FILE_LIMIT]
    parts: list[str] = ["## 最近读过的文件"]
    if not files:
        parts.append("(无)")
    else:
        for rec in files:
            parts.append(render_file_block(rec).rstrip("\n"))
    parts.append("")
    parts.append("## 当前可用工具")
    parts.append(render_tools_block(tool_defs))
    parts.append("")
    parts.append("## 边界提示")
    parts.append(BOUNDARY_NOTICE)
    return "\n".join(parts) + "\n"
