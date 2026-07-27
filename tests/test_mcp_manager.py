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


async def _stub_ok(mgr, name, srv, version, done, tool=None) -> None:
    """stub：成功注册 tool + 持守到 close。"""
    if tool is not None:
        await _register_tools(mgr, name, None, [tool])  # type: ignore[arg-type]
    done.set()
    await mgr._close_event.wait()


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
    real_connect = manager_mod._connect_one

    async def routed(mgr, name, srv, version, done):
        if name == "stub":
            await _stub_ok(mgr, name, srv, version, done, tool=stub_tool)
        else:
            await real_connect(mgr, name, srv, version, done)

    monkeypatch.setattr(manager_mod, "_connect_one", routed)

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

    async def hanging(mgr, name, srv, version, done):
        await mgr._close_event.wait()  # 不 set done，模拟握手卡住

    monkeypatch.setattr(manager_mod, "_connect_one", hanging)

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

    async def close_hang(mgr, name, srv, version, done):
        done.set()
        await mgr._close_event.wait()
        await asyncio.Event().wait()  # close 通知后仍卡住（模拟 __aexit__ 阻塞）

    monkeypatch.setattr(manager_mod, "_connect_one", close_hang)

    cfg = Config(servers={"srv": ServerConfig(type="stdio", command="x")})
    mgr = await new_manager(cfg, "0.0.0-test")
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

    async def stub(mgr, name, srv, version, done):
        # alpha 故意后完成，验证结果仍按 full_name 排序而非完成顺序
        if name == "alpha":
            await asyncio.sleep(0.05)
        await _register_tools(mgr, name, None, [_mktool(names_to_full[name])])  # type: ignore[arg-type]
        done.set()
        await mgr._close_event.wait()

    monkeypatch.setattr(manager_mod, "_connect_one", stub)

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
    tool_a = _mktool("mcp__shared__t")
    tool_b = _mktool("mcp__shared__t")  # 同名（防御性场景）
    await _register_tools(mgr, "a", None, [tool_a])  # type: ignore[arg-type]
    await _register_tools(mgr, "b", None, [tool_b])  # type: ignore[arg-type]  # 后到者跳过
    assert len(mgr.tools()) == 1
    err = capsys.readouterr().err
    assert "skip duplicate tool mcp__shared__t" in err
