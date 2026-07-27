"""MCP 客户端：配置加载、工具适配、连接管理。

对外暴露 :class:`Config` / :class:`ServerConfig` / :func:`load_config`
（T2）；:class:`Manager` / :func:`new_manager` / :class:`McpTool` 在 T4 追加。
"""

from koyocode.mcp.config import Config, ServerConfig, load_config

__all__ = ["Config", "ServerConfig", "load_config"]
