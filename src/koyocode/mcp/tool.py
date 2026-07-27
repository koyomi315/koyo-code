"""MCP 工具适配：把 SDK 返回的远端工具包装成 koyocode ``Tool`` 协议。

- :func:`adapt_tool` 给远端工具加 ``mcp__<server>__<tool>`` 前缀并校验命名合法性。
- :class:`McpTool` 实现与内置工具同款的无参方法接口（``name`` / ``description``
  / ``parameters`` / ``read_only`` / ``execute``），调用时通过持有会话发
  ``call_tool``；远端文本块拼成 ``ToolResult.content``，协议错 / 超时 / 远端
  ``isError`` 均转成结构化错误回灌，不向 Agent Loop 抛异常（F7/F10）。
"""

import asyncio
import json
import re
import sys
from typing import Any, Protocol

import mcp.types as mtypes

from koyocode.tool import ToolResult

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
"""LLM 工具名安全字符集（F8）。"""

_call_timeout: float = 30.0
"""单次 call_tool 超时秒数（F10，内置不可配；模块级变量便于单测改小）。"""

# 非 text 内容块告警去重：同一 full_name 限告警一次。
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    """McpTool 持有的会话协议（便于单测注入 stub）。"""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> mtypes.CallToolResult: ...


class McpTool:
    """实现 koyocode ``Tool`` 协议的远端工具包装。

    用 ``_`` 前缀字段存值、同名无参方法返回（对齐内置工具）；不用 dataclass--
    其字段与方法同名会冲突。
    """

    def __init__(
        self,
        full_name: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        read_only: bool,
        caller: CallerSession,
    ) -> None:
        self._full_name = full_name  # "mcp__<server>__<tool>"
        self.remote_name = remote_name  # server 上的原始工具名
        self._description = description
        self._parameters = parameters  # JSON Schema 透传
        self._read_only = read_only  # 仅来自 annotations.readOnlyHint==True
        self.caller = caller  # 协议形式持有，便于单测注入 stub

    def name(self) -> str:
        return self._full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, args: str) -> ToolResult:
        # args 是 raw JSON 字符串（与内置工具签名一致）；空串视作无参数。
        try:
            arg_map = json.loads(args) if args and args.strip() else None
        except json.JSONDecodeError as e:
            return ToolResult(content=f"MCP 工具参数解析失败: {e}", is_error=True)
        try:
            result = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, arg_map),
                timeout=_call_timeout,
            )
        except TimeoutError:
            return ToolResult(content="MCP 工具调用超时 (30s)", is_error=True)
        except Exception as e:  # noqa: BLE001 - 任意协议错均转结构化错误回灌
            return ToolResult(content=f"MCP 工具调用失败: {e}", is_error=True)

        texts: list[str] = []
        for block in result.content:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            else:
                # 非 text 块（image/audio/resource_link/embedded_resource）静默丢弃，限告警一次。
                if self._full_name not in _non_text_warn_once:
                    _non_text_warn_once.add(self._full_name)
                    print(
                        f"[mcp] warn: tool {self._full_name} returned non-text "
                        f"content blocks (dropped)",
                        file=sys.stderr,
                    )
        return ToolResult(content="\n".join(texts), is_error=bool(result.isError))


def adapt_tool(server_name: str, t: mtypes.Tool, session: CallerSession) -> McpTool | None:
    """把远端工具适配为 :class:`McpTool`；命名非法返回 None + 告警。"""
    full_name = f"mcp__{server_name}__{t.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None
    description = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"
    # inputSchema 已是 dict[str, Any]；浅拷贝避免污染远端对象；空兜底防 provider 拒收。
    schema = t.inputSchema
    parameters = dict(schema) if schema else {"type": "object"}
    read_only = bool(t.annotations and t.annotations.readOnlyHint)
    return McpTool(
        full_name=full_name,
        remote_name=t.name,
        description=description,
        parameters=parameters,
        read_only=read_only,
        caller=session,
    )
