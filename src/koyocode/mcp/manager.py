"""MCP 连接管理器：并发连接所有 server、缓存会话、统一关闭。

- :func:`new_manager` 并发（``asyncio.gather``）连接配置中的每个 server，每个
  server 受 ``connect_timeout``（30s）超时约束，失败 / 超时仅跳过该 server + 告警，
  不阻断启动（F9/N1）。
- :class:`Manager` 用单一 :class:`AsyncExitStack` 持有所有 transport 与
  ``ClientSession`` 上下文；``close`` 经 ``aclose`` 统一收尾，``close_timeout``
  （5s）兜底防卡死（F11/N7）。
- AsyncExitStack 并发安全：``enter_async_context`` 在 ``_lock`` 内串行（其内部
  列表非并发安全、``__aenter__`` 有 await 点）；握手 / 列工具在锁外并发以缩短
  总时延（F9）；去重 + 注册在 ``_lock`` 内保证原子性。
"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass

import mcp.types as mtypes
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from koyocode.mcp.config import Config, ServerConfig
from koyocode.mcp.tool import McpTool, adapt_tool

connect_timeout: float = 30.0
"""单 server 连接 + 握手 + 列工具的超时秒数（F9，模块级便于单测改小）。"""

close_timeout: float = 5.0
"""统一关闭的兜底超时秒数（F11，模块级便于单测改小）。"""


@dataclass
class _Session:
    """成功建立的会话记录（供参照，关闭由 AsyncExitStack 统一收尾）。"""

    name: str
    session: ClientSession


class Manager:
    """MCP server 连接管理器（对外不透明）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []  # 成功建立的会话
        self._tools: list[McpTool] = []  # 适配好的工具（供 cli 注册）
        self._stack = AsyncExitStack()  # 持有 stdio / http 上下文

    def tools(self) -> list[McpTool]:
        """返回适配好的工具列表（按 full_name 排序）的副本。"""
        return list(self._tools)

    async def close(self) -> None:
        """关闭所有会话；总超时 close_timeout 兜底，绝不阻塞退出。"""
        try:
            await asyncio.wait_for(self._stack.aclose(), timeout=close_timeout)
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )


async def new_manager(cfg: Config, version: str) -> Manager:
    """并发连接所有 server，每个受 connect_timeout 超时约束，失败仅跳过 + 告警。

    阻塞直到所有 server 的尝试结束（成功 / 失败 / 超时）。
    """
    mgr = Manager()
    await mgr._stack.__aenter__()  # 进入 stack，供 enter_async_context 挂载上下文
    if not cfg.servers:
        return mgr
    tasks = [
        asyncio.create_task(_connect_one(mgr, name, srv, version))
        for name, srv in cfg.servers.items()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    mgr._tools.sort(key=lambda t: t.name())
    return mgr


async def _connect_one(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    """单个 server 的连接尝试：超时 / 异常均吸收 + 告警，不阻断其它 server。"""
    try:
        await asyncio.wait_for(_do_connect(mgr, name, srv, version), timeout=connect_timeout)
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout}s",
            file=sys.stderr,
        )
    except Exception as e:  # noqa: BLE001 - 任意连接错均跳过该 server
        print(f"[mcp] warn: connect server {name} failed: {e}", file=sys.stderr)


async def _do_connect(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    """建立 transport + ClientSession，握手 + 列工具 + 适配注册。"""
    if srv.type == "stdio":
        params = StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env={**os.environ, **srv.env},  # 同名宿主变量被 env 覆盖
        )
        ctx = stdio_client(params)
    else:  # http
        ctx = streamablehttp_client(srv.url, headers=srv.headers or None)

    # 锁内：进入 transport + session 上下文（AsyncExitStack 并发不安全，需串行 enter）
    async with mgr._lock:
        transport = await mgr._stack.enter_async_context(ctx)
        read, write = transport[0], transport[1]  # http 返回 3 元组，第三个是 metadata
        session = await mgr._stack.enter_async_context(
            ClientSession(
                read,
                write,
                client_info=mtypes.Implementation(name="koyocode", version=version),
            )
        )

    # 锁外：握手 + 列工具（各 server 独立 session，可并发，缩短总时延 F9）
    await session.initialize()
    listed = await session.list_tools()

    adapted: list[McpTool] = []
    for t in listed.tools:
        tool = adapt_tool(name, t, session)
        if tool is None:
            continue
        adapted.append(tool)

    await _register_tools(mgr, name, session, adapted)


async def _register_tools(
    mgr: Manager, name: str, session: ClientSession, adapted: list[McpTool]
) -> None:
    """锁内按 full_name 去重 + 注册工具与会话。

    ``Registry.register`` 遇重名抛 ``ValueError`` 会中断启动，故必须在 Manager
    层消化重名（F8）。去重是防御性的：正常情况下 full_name 全局唯一。
    """
    async with mgr._lock:
        existing = {t.name() for t in mgr._tools}
        for tool in adapted:
            if tool.name() in existing:
                print(f"[mcp] warn: skip duplicate tool {tool.name()}", file=sys.stderr)
                continue
            existing.add(tool.name())
            mgr._tools.append(tool)
        mgr._sessions.append(_Session(name=name, session=session))
