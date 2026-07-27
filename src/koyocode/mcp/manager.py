"""MCP 连接管理器：并发连接所有 server、缓存会话、统一关闭。

- :func:`new_manager` 并发（``asyncio.gather``）连接配置中的每个 server，每个
  server 受 ``connect_timeout``（30s）超时约束，失败 / 超时仅跳过该 server + 告警，
  不阻断启动（F9/N1）。
- :class:`Manager` 的 ``close`` 通过 ``close_event`` 通知各连接 task 退出各自的
  ``async with`` 上下文来统一收尾（stdio 子进程终止 / HTTP 会话关闭），
  ``close_timeout``（5s）兜底防卡死，超时则取消未完成任务避免泄漏（F11/N7）。
- 上下文生命周期：每个 server 的 transport + ClientSession 在其 ``_connect_one``
  task 内用 ``async with`` 进入与退出--anyio cancel_scope 要求 enter/exit 同 task，
  故不能跨 task 用单一 ``AsyncExitStack`` 收尾。
"""

import asyncio
import os
import sys
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
    """成功建立的会话记录（供参照，关闭由各 task 的 async with 收尾）。"""

    name: str
    session: ClientSession


class Manager:
    """MCP server 连接管理器（对外不透明）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []  # 成功建立的会话
        self._tools: list[McpTool] = []  # 适配好的工具（供 cli 注册）
        self._close_event: asyncio.Event = asyncio.Event()  # 通知各 task 退出
        self._tasks: list[asyncio.Task[None]] = []  # 各 server 的连接 task

    def tools(self) -> list[McpTool]:
        """返回适配好的工具列表（按 full_name 排序）的副本。"""
        return list(self._tools)

    async def close(self) -> None:
        """通知所有连接 task 退出各自上下文；close_timeout 兜底，超时取消未完成任务。"""
        self._close_event.set()
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=close_timeout,
            )
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def new_manager(cfg: Config, version: str) -> Manager:
    """并发连接所有 server，每个受 connect_timeout 超时约束，失败仅跳过 + 告警。

    阻塞直到所有 server 的握手尝试结束（成功 / 失败 / 超时）；连接 task 持守到
    ``close`` 通知后才退出（以在本 task 内退出上下文）。
    """
    mgr = Manager()
    if not cfg.servers:
        return mgr
    handshake_done: list[tuple[str, asyncio.Event]] = []
    for name, srv in cfg.servers.items():
        done = asyncio.Event()
        task = asyncio.create_task(_connect_one(mgr, name, srv, version, done))
        mgr._tasks.append(task)
        handshake_done.append((name, done))
    # 并发等所有握手完成，每个独立受 connect_timeout 超时约束
    await asyncio.gather(*[_wait_handshake(name, done) for name, done in handshake_done])
    mgr._tools.sort(key=lambda t: t.name())
    return mgr


async def _wait_handshake(name: str, done: asyncio.Event) -> None:
    """等单个 server 握手完成（done 被 set），超时告警（不抛出）。"""
    try:
        await asyncio.wait_for(done.wait(), timeout=connect_timeout)
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout}s",
            file=sys.stderr,
        )


async def _connect_one(
    mgr: Manager, name: str, srv: ServerConfig, version: str, done: asyncio.Event
) -> None:
    """单个 server：enter transport+session -> 握手 -> 列工具 -> 注册 -> 持守到 close。

    enter/exit 在本 task 内成对（``async with``），满足 anyio cancel_scope 同 task
    约束。任意阶段失败 -> 告警 + ``done.set``（避免 new_manager 卡）。
    """
    try:
        if srv.type == "stdio":
            params = StdioServerParameters(
                command=srv.command,
                args=srv.args,
                env={**os.environ, **srv.env},  # 同名宿主变量被 env 覆盖
            )
            ctx = stdio_client(params)
        else:  # http
            ctx = streamablehttp_client(srv.url, headers=srv.headers or None)
        async with ctx as transport:
            read, write = transport[0], transport[1]  # http 返回 3 元组，第三个是 metadata
            async with ClientSession(
                read,
                write,
                client_info=mtypes.Implementation(name="koyocode", version=version),
            ) as session:
                await session.initialize()  # 握手
                listed = await session.list_tools()
                adapted: list[McpTool] = []
                for t in listed.tools:
                    tool = adapt_tool(name, t, session)
                    if tool is not None:
                        adapted.append(tool)
                await _register_tools(mgr, name, session, adapted)
                done.set()  # 握手 + 注册完成
                await mgr._close_event.wait()  # 持守，等 close 通知后退出上下文
    except Exception as e:  # noqa: BLE001 - 任意连接错均跳过该 server
        print(f"[mcp] warn: connect server {name} failed: {e}", file=sys.stderr)
        done.set()


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
