"""mcp.tool 模块单测：命名拼接 / 禁用字符 / Execute 各分支。"""

import asyncio
import json

import mcp.types as mtypes
import pytest

from koyocode.mcp import tool as tool_mod
from koyocode.mcp.tool import McpTool, adapt_tool
from koyocode.tool import ToolResult


class StubSession:
    """可配置的 CallerSession stub：按预设返回结果 / 抛异常 / 阻塞。"""

    def __init__(self, result=None, exc=None, block=False) -> None:
        self._result = result
        self._exc = exc
        self._block = block
        self.called_name = None
        self.called_args = None

    async def call_tool(self, name: str, arguments: dict | None):
        self.called_name = name
        self.called_args = arguments
        if self._exc is not None:
            raise self._exc
        if self._block:
            await asyncio.Event().wait()  # 永远阻塞
        return self._result


def _make_tool(
    name: str = "echo",
    description: str | None = "echo tool",
    schema: dict | None = None,
    annotations: mtypes.ToolAnnotations | None = None,
) -> mtypes.Tool:
    return mtypes.Tool(
        name=name,
        description=description,
        inputSchema=schema if schema is not None else {"type": "object"},
        annotations=annotations,
    )


# --- adapt_tool ---


def test_adapt_tool_valid() -> None:
    session = StubSession()
    t = _make_tool(
        name="echo", description="echo back", schema={"type": "object", "properties": {}}
    )
    tool = adapt_tool("demo", t, session)
    assert isinstance(tool, McpTool)
    assert tool.name() == "mcp__demo__echo"
    assert tool.remote_name == "echo"
    assert tool.description() == "echo back"
    assert tool.parameters() == {"type": "object", "properties": {}}
    assert tool.read_only() is False


def test_adapt_tool_illegal_server_name_returns_none(capsys) -> None:
    session = StubSession()
    t = _make_tool(name="echo")
    # server 名含 '.' -> full_name 含非法字符
    tool = adapt_tool("demo.host", t, session)
    assert tool is None
    err = capsys.readouterr().err
    assert "skip tool" in err
    assert "illegal characters" in err


def test_adapt_tool_illegal_tool_name_returns_none(capsys) -> None:
    session = StubSession()
    t = _make_tool(name="echo@v2")  # 工具名含 '@'
    tool = adapt_tool("demo", t, session)
    assert tool is None
    err = capsys.readouterr().err
    assert "illegal characters" in err


def test_adapt_tool_description_fallback() -> None:
    session = StubSession()
    t = _make_tool(name="echo", description=None)
    tool = adapt_tool("demo", t, session)
    assert "来自 MCP server demo 的工具 echo" in tool.description()


def test_adapt_tool_schema_empty_fallback() -> None:
    session = StubSession()
    # inputSchema 为空 dict -> 兜底 {"type": "object"}
    t = mtypes.Tool(name="echo", description="d", inputSchema={})
    tool = adapt_tool("demo", t, session)
    assert tool.parameters() == {"type": "object"}


def test_adapt_tool_schema_passthrough() -> None:
    session = StubSession()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    t = _make_tool(schema=schema)
    tool = adapt_tool("demo", t, session)
    assert tool.parameters() == schema
    # 浅拷贝：修改返回值不影响原始 schema
    tool.parameters()["foo"] = "bar"
    assert "foo" not in schema


def test_adapt_tool_read_only_annotations_none() -> None:
    session = StubSession()
    t = _make_tool(annotations=None)
    tool = adapt_tool("demo", t, session)
    assert tool.read_only() is False


def test_adapt_tool_read_only_true() -> None:
    session = StubSession()
    t = _make_tool(annotations=mtypes.ToolAnnotations(readOnlyHint=True))
    tool = adapt_tool("demo", t, session)
    assert tool.read_only() is True


def test_adapt_tool_read_only_false() -> None:
    session = StubSession()
    t = _make_tool(annotations=mtypes.ToolAnnotations(readOnlyHint=False))
    tool = adapt_tool("demo", t, session)
    assert tool.read_only() is False


# --- execute ---


@pytest.mark.asyncio
async def test_execute_success_multi_text() -> None:
    result = mtypes.CallToolResult(
        content=[
            mtypes.TextContent(type="text", text="line1"),
            mtypes.TextContent(type="text", text="line2"),
        ],
        isError=False,
    )
    session = StubSession(result=result)
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute(json.dumps({"msg": "hi"}))
    assert isinstance(tr, ToolResult)
    assert tr.is_error is False
    assert tr.content == "line1\nline2"
    assert session.called_name == "echo"
    assert session.called_args == {"msg": "hi"}


@pytest.mark.asyncio
async def test_execute_remote_iserror() -> None:
    result = mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text="boom")],
        isError=True,
    )
    session = StubSession(result=result)
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("{}")
    assert tr.is_error is True
    assert tr.content == "boom"


@pytest.mark.asyncio
async def test_execute_call_raises() -> None:
    session = StubSession(exc=RuntimeError("connection reset"))
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("{}")
    assert tr.is_error is True
    assert "MCP 工具调用失败" in tr.content
    assert "connection reset" in tr.content


@pytest.mark.asyncio
async def test_execute_timeout(monkeypatch) -> None:
    monkeypatch.setattr(tool_mod, "_call_timeout", 0.2)
    session = StubSession(block=True)
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("{}")
    assert tr.is_error is True
    assert "超时" in tr.content


@pytest.mark.asyncio
async def test_execute_bad_json() -> None:
    session = StubSession()
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("not json")
    assert tr.is_error is True
    assert "参数解析失败" in tr.content


@pytest.mark.asyncio
async def test_execute_empty_args_as_none() -> None:
    result = mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text="ok")], isError=False
    )
    session = StubSession(result=result)
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("")
    assert tr.is_error is False
    assert tr.content == "ok"
    assert session.called_args is None  # 空串视作无参数


@pytest.mark.asyncio
async def test_execute_non_text_blocks_dropped(capsys, monkeypatch) -> None:
    monkeypatch.setattr(tool_mod, "_non_text_warn_once", set())  # 隔离 once set
    result = mtypes.CallToolResult(
        content=[
            mtypes.TextContent(type="text", text="keep"),
            mtypes.ImageContent(type="image", data="AAAA", mimeType="image/png"),
        ],
        isError=False,
    )
    session = StubSession(result=result)
    tool = adapt_tool("demo", _make_tool(), session)
    tr = await tool.execute("{}")
    assert tr.content == "keep"  # 非 text 块丢弃
    err = capsys.readouterr().err
    assert "non-text content blocks" in err


@pytest.mark.asyncio
async def test_execute_non_text_warn_once(capsys, monkeypatch) -> None:
    monkeypatch.setattr(tool_mod, "_non_text_warn_once", set())  # 隔离 once set
    result = mtypes.CallToolResult(
        content=[mtypes.ImageContent(type="image", data="AAAA", mimeType="image/png")],
        isError=False,
    )
    session = StubSession(result=result)
    tool = adapt_tool("demo", _make_tool(), session)
    await tool.execute("{}")
    await tool.execute("{}")
    err = capsys.readouterr().err
    assert err.count("non-text content blocks") == 1  # 同 full_name 限一次
