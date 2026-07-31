"""recovery.py 三段恢复单测。"""

from __future__ import annotations

import json
from datetime import datetime

from koyocode.compact.recovery import build_recovery_attachment, render_file_block
from koyocode.compact.state import FileReadRecord
from koyocode.llm import ToolDefinition


def test_render_file_block_truncate():
    rec = FileReadRecord(path="/a.txt", content="X" * 20000, timestamp=datetime.now())
    b = render_file_block(rec)
    assert "(content truncated)" in b
    assert b.count("X") == 17500  # 头部保留 17500，尾部截掉


def test_render_file_block_no_truncate():
    rec = FileReadRecord(path="/a.txt", content="short", timestamp=datetime.now())
    b = render_file_block(rec)
    assert "(content truncated)" not in b
    assert "short" in b


def test_build_recovery_attachment_limit():
    recs = [
        FileReadRecord(path=f"/f{i}.txt", content="c", timestamp=datetime.now()) for i in range(7)
    ]
    defs = [ToolDefinition(name="read_file", description="read", input_schema={"type": "object"})]
    text = build_recovery_attachment(recs, defs)
    assert "最近读过的文件" in text
    assert "当前可用工具" in text
    assert "边界提示" in text
    for i in range(5):
        assert f"/f{i}.txt" in text
    assert "/f5.txt" not in text
    assert "/f6.txt" not in text
    # 5 条按时间戳倒序（同 timestamp 保持插入顺序）
    for i in range(4):
        assert text.index(f"/f{i}.txt") < text.index(f"/f{i + 1}.txt")


def test_build_recovery_attachment_tools_exact():
    defs = [
        ToolDefinition(
            name="read_file",
            description="read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        ToolDefinition(name="bash", description="run shell", input_schema={"type": "object"}),
    ]
    text = build_recovery_attachment([], defs)
    for d in defs:
        assert f"- {d.name}:" in text
    # 每个工具的 schema 行可 json.loads 还原
    schema_strs = [line.split("schema: ", 1)[1] for line in text.splitlines() if "schema: " in line]
    assert len(schema_strs) == len(defs)
    for s, d in zip(schema_strs, defs, strict=True):
        assert json.loads(s) == d.input_schema


def test_boundary_notice_stable():
    defs = [ToolDefinition(name="t", description="d", input_schema={})]
    a = build_recovery_attachment([], defs)
    b = build_recovery_attachment([], defs)
    assert a == b
