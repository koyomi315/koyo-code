"""provider 协议默认上下文窗口大小（token）。

未配置 ``context_window`` 时按协议取此处的默认值。常量定义在 config 自身，
不依赖 compact 子包，保证 config -> compact 单向无环依赖。
"""

DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128000
