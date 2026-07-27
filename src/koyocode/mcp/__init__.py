"""MCP 客户端：配置加载、工具适配、连接管理。

对外暴露 :class:`Config` / :class:`ServerConfig` / :func:`load_config`、
:class:`Manager` / :func:`new_manager` / :class:`McpTool`。
"""

from koyocode.mcp.config import Config, ServerConfig, load_config
from koyocode.mcp.manager import Manager, new_manager
from koyocode.mcp.tool import McpTool

__all__ = [
    "Config",
    "Manager",
    "McpTool",
    "ServerConfig",
    "load_config",
    "new_manager",
]
