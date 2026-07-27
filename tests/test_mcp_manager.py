"""mcp.manager 模块单测：连接成功/失败/超时、close 不死锁、共享状态并发安全。"""

import asyncio
import time

import pytest

from koyocode.mcp import manager as manager_mod
from koyocode.mcp.config import Config, ServerConfig
from koyocode.mcp.manager import Manager, _register_tools, new_manager
from koyocode.mcp.tool import McpTool


def _mktool(full_name: str) -> McpTool:
    """构造一个不调用的 McpTool（caller=None，仅供注册/排序测试）。"""
    return McpTool(
        full_name=full_name,
        remote_name="t",
        description="d",
        parameters={"type": "object"},
        read_only=False,
        caller=None,  # type: ignore[arg-type]
    )


# --- 空配置 ---


@pytest.mark.asyncio
async def test_empty_config() -> None:
    cfg = Config()
    mgr = await new_manager(cfg, "0.0.0-test")
    assert isinstance(mgr, Manager)
    assert mgr.tools() == []
    await mgr.close()  # 立即返回


# --- 失败隔离 ---


@pytest.mark.asyncio
async def test_failed_server_isolated(monkeypatch, capsys) -> None:
    stub_tool = _mktool("mcp__stub__echo")

    async def routed_do_connect(mgr, name, srv, version):
        if name == "bad":
            raise RuntimeError("connection refused")  # 模拟连接失败
        # stub 成功：直接注册
        await _register_tools(mgr, name, None, [stub_tool])  # type: ignore[arg-type]

    monkeypatch.setattr(manager_mod, "_do_connect", routed_do_connect)

    cfg = Config(
        servers={
            "bad": ServerConfig(type="stdio", command="/no/such/bin"),
            "stub": ServerConfig(type="stdio", command="echo"),
        }
    )
    mgr = await new_manager(cfg, "0.0.0-test")
    tools = mgr.tools()
    assert len(tools) == 1
    assert tools[0].name() == "mcp__stub__echo"
    err = capsys.readouterr().err
    assert "connect server bad failed" in err
    await mgr.close()


# --- 超时收尾 ---


@pytest.mark.asyncio
async def test_connect_timeout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(manager_mod, "connect_timeout", 0.2)

    async def hanging_connect(mgr, name, srv, version):
        await asyncio.Event().wait()  # 永远阻塞

    monkeypatch.setattr(manager_mod, "_do_connect", hanging_connect)

    cfg = Config(servers={"srv": ServerConfig(type="stdio", command="x")})
    start = time.monotonic()
    mgr = await new_manager(cfg, "0.0.0-test")
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # 远小于 30s，~0.2s 超时收尾
    assert mgr.tools() == []
    err = capsys.readouterr().err
    assert "timeout" in err
    await mgr.close()


# --- close 兜底 ---


@pytest.mark.asyncio
async def test_close_timeout(monkeypatch) -> None:
    monkeypatch.setattr(manager_mod, "close_timeout", 0.2)

    cfg = Config()
    mgr = await new_manager(cfg, "0.0.0-test")

    class HangingCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await asyncio.Event().wait()  # 永远阻塞

    await mgr._stack.enter_async_context(HangingCM())

    start = time.monotonic()
    await mgr.close()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # ~0.2s 兜底返回


# --- 并发安全：_tools 顺序由 sort 决定，而非 task 完成顺序 ---


@pytest.mark.asyncio
async def test_tools_sorted_by_name(monkeypatch) -> None:
    names_to_full = {
        "zeta": "mcp__zeta__t",
        "alpha": "mcp__alpha__t",
        "mid": "mcp__mid__t",
    }

    async def stub_connect(mgr, name, srv, version):
        # alpha 故意后完成，验证结果仍按 full_name 排序而非完成顺序
        if name == "alpha":
            await asyncio.sleep(0.05)
        await _register_tools(mgr, name, None, [_mktool(names_to_full[name])])  # type: ignore[arg-type]

    monkeypatch.setattr(manager_mod, "_do_connect", stub_connect)

    cfg = Config(
        servers={
            "zeta": ServerConfig(type="stdio", command="z"),
            "alpha": ServerConfig(type="stdio", command="a"),
            "mid": ServerConfig(type="stdio", command="m"),
        }
    )
    mgr = await new_manager(cfg, "0.0.0-test")
    full_names = [t.name() for t in mgr.tools()]
    assert full_names == ["mcp__alpha__t", "mcp__mid__t", "mcp__zeta__t"]  # 排序
    await mgr.close()


# --- 跨 server 同名 tool 去重 ---


@pytest.mark.asyncio
async def test_register_tools_dedup(capsys) -> None:
    mgr = Manager()
    await mgr._stack.__aenter__()
    tool_a = _mktool("mcp__shared__t")
    tool_b = _mktool("mcp__shared__t")  # 同名（防御性场景）
    await _register_tools(mgr, "a", None, [tool_a])  # type: ignore[arg-type]
    await _register_tools(mgr, "b", None, [tool_b])  # type: ignore[arg-type]  # 后到者跳过
    assert len(mgr.tools()) == 1
    err = capsys.readouterr().err
    assert "skip duplicate tool mcp__shared__t" in err
    await mgr.close()
