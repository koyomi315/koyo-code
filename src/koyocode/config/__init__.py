"""配置加载与校验：从 YAML 读取 providers 列表。

重导出 ``ProviderConfig`` / ``Config`` / ``load`` / ``ConfigError`` /
``effective_context_window`` 与协议默认窗口常量，保持 ``from koyocode.config import ...``
对调用方兼容。
"""

from koyocode.config.config import (
    Config,
    ConfigError,
    ProviderConfig,
    effective_context_window,
    load,
)
from koyocode.config.protocol_defaults import (
    DEFAULT_ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_OPENAI_CONTEXT_WINDOW,
)

__all__ = [
    "Config",
    "ConfigError",
    "DEFAULT_ANTHROPIC_CONTEXT_WINDOW",
    "DEFAULT_OPENAI_CONTEXT_WINDOW",
    "ProviderConfig",
    "effective_context_window",
    "load",
]
