"""tool 包单测：注册中心 + 6 个核心工具（AC1–AC6）。

跨平台：bash 超时测试按 ``sys.platform`` 选长跑命令（Windows 用 ping，类 Unix 用 sleep）。
"""

import json
import sys
from pathlib import Path

import pytest

from koyocode.tool import Registry, new_default_registry
from koyocode.tool.bash import BashTool
from koyocode.tool.edit_file import EditFileTool
from koyocode.tool.glob_tool import GlobTool
from koyocode.tool.grep_tool import GrepTool
from koyocode.tool.read_file import ReadFileTool
from koyocode.tool.write_file import WriteFileTool

# ───────── 注册中心（AC1）─────────


def test_registry_definitions_six_ordered() -> None:
    reg = new_default_registry()
    defs = reg.definitions()
    assert len(defs) == 6
    expected = ["read_file", "write_file", "edit_file", "bash", "glob", "grep"]
    assert [d.name for d in defs] == expected
    for d in defs:
        assert d.description
        assert d.input_schema["type"] == "object"
        assert "properties" in d.input_schema


def test_registry_get_hit_and_miss() -> None:
    reg = new_default_registry()
    assert reg.get("bash") is not None
    assert reg.get("nope") is None


def test_registry_read_only_definitions_and_is_read_only() -> None:
    reg = new_default_registry()
    assert [d.name for d in reg.read_only_definitions()] == ["read_file", "glob", "grep"]
    for name in ("read_file", "glob", "grep"):
        assert reg.is_read_only(name) is True
    for name in ("write_file", "edit_file", "bash"):
        assert reg.is_read_only(name) is False
    assert reg.is_read_only("nope") is False


def test_registry_duplicate_raises() -> None:
    reg = Registry()
    reg.register(ReadFileTool())
    with pytest.raises(ValueError, match="已注册"):
        reg.register(ReadFileTool())


async def test_registry_unknown_tool_is_error() -> None:
    reg = new_default_registry()
    r = await reg.execute("nope", "{}")
    assert r.is_error
    assert "未知工具" in r.content


# ───────── read_file（AC2）─────────


async def test_read_file_with_line_numbers(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")
    r = await ReadFileTool().execute(json.dumps({"path": str(f)}))
    assert not r.is_error
    assert "1\tline1" in r.content
    assert "2\tline2" in r.content


async def test_read_file_missing_is_error() -> None:
    r = await ReadFileTool().execute(json.dumps({"path": "nope.xyz"}))
    assert r.is_error
    assert "不存在" in r.content


async def test_read_file_directory_is_error(tmp_path: Path) -> None:
    r = await ReadFileTool().execute(json.dumps({"path": str(tmp_path)}))
    assert r.is_error
    assert "目录" in r.content


# ───────── write_file（AC3）─────────


async def test_write_file_new_and_nested(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    r = await WriteFileTool().execute(json.dumps({"path": str(nested), "content": "hi"}))
    assert not r.is_error
    assert nested.read_text(encoding="utf-8") == "hi"


async def test_write_file_overwrite(tmp_path: Path) -> None:
    f = tmp_path / "o.txt"
    f.write_text("old", encoding="utf-8")
    await WriteFileTool().execute(json.dumps({"path": str(f), "content": "new"}))
    assert f.read_text(encoding="utf-8") == "new"


# ───────── edit_file（AC4）─────────


async def test_edit_file_unique_match(tmp_path: Path) -> None:
    f = tmp_path / "e.txt"
    f.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    r = await EditFileTool().execute(
        json.dumps({"path": str(f), "old_string": "bar", "new_string": "BAZ"})
    )
    assert not r.is_error
    assert f.read_text(encoding="utf-8") == "foo\nBAZ\nfoo\n"


async def test_edit_file_no_match(tmp_path: Path) -> None:
    f = tmp_path / "e.txt"
    f.write_text("foo\n", encoding="utf-8")
    r = await EditFileTool().execute(
        json.dumps({"path": str(f), "old_string": "NOPE", "new_string": "x"})
    )
    assert r.is_error
    assert "未找到" in r.content
    assert f.read_text(encoding="utf-8") == "foo\n"


async def test_edit_file_multi_match_includes_count(tmp_path: Path) -> None:
    f = tmp_path / "e.txt"
    f.write_text("dup\ndup\n", encoding="utf-8")
    r = await EditFileTool().execute(
        json.dumps({"path": str(f), "old_string": "dup", "new_string": "x"})
    )
    assert r.is_error
    assert "2" in r.content
    assert "不唯一" in r.content
    # 文件未被修改
    assert f.read_text(encoding="utf-8") == "dup\ndup\n"


async def test_edit_file_error_messages_distinguishable(tmp_path: Path) -> None:
    """0 处与多于 1 处的错误文案可区分（AC4）。"""
    f = tmp_path / "e.txt"
    f.write_text("dup\ndup\n", encoding="utf-8")
    r0 = await EditFileTool().execute(
        json.dumps({"path": str(f), "old_string": "NOPE", "new_string": "x"})
    )
    rm = await EditFileTool().execute(
        json.dumps({"path": str(f), "old_string": "dup", "new_string": "x"})
    )
    assert r0.content != rm.content
    assert "未找到" in r0.content
    assert "不唯一" in rm.content


# ───────── bash（AC5/N1）─────────


def _long_cmd() -> str:
    """跨平台长跑命令，用于超时测试。"""
    return "ping -n 10 127.0.0.1" if sys.platform == "win32" else "sleep 5"


async def test_bash_echo() -> None:
    r = await BashTool().execute(json.dumps({"command": "echo hi"}))
    assert not r.is_error
    assert "hi" in r.content
    assert "exit_code: 0" in r.content


async def test_bash_timeout_returns_structured_error() -> None:
    reg = Registry()
    reg.register(BashTool())
    r = await reg.execute("bash", json.dumps({"command": _long_cmd()}), timeout=0.5)
    assert r.is_error
    assert "超时" in r.content


# ───────── glob / grep（AC6）─────────


async def test_glob_py_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("x", encoding="utf-8")
    r = await GlobTool().execute(json.dumps({"pattern": "**/*.py", "path": str(tmp_path)}))
    assert not r.is_error
    assert "a.py" in r.content
    assert "c.py" in r.content
    assert "b.txt" not in r.content


async def test_glob_no_match_not_error(tmp_path: Path) -> None:
    r = await GlobTool().execute(json.dumps({"pattern": "*.nothing", "path": str(tmp_path)}))
    assert not r.is_error
    assert "无匹配" in r.content


async def test_grep_keyword(tmp_path: Path) -> None:
    f = tmp_path / "g.py"
    f.write_text("print('hello')\nprint('world')\n", encoding="utf-8")
    r = await GrepTool().execute(json.dumps({"pattern": "hello", "path": str(tmp_path)}))
    assert not r.is_error
    assert "hello" in r.content
    assert ":1:" in r.content


async def test_grep_no_match_not_error(tmp_path: Path) -> None:
    r = await GrepTool().execute(json.dumps({"pattern": "NEVER_HERE_xyz", "path": str(tmp_path)}))
    assert not r.is_error
    assert "无命中" in r.content


async def test_grep_invalid_regex_is_error() -> None:
    r = await GrepTool().execute(json.dumps({"pattern": "(unclosed"}))
    assert r.is_error
    assert "正则非法" in r.content
