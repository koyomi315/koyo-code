"""内置 system prompt 与启动 banner（ASCII 猫）。"""

SYSTEM_PROMPT = """\
你是 KoyoCode，一个运行在终端中的 AI 编程助手。你耐心、简洁、务实。
- 用与用户提问相同的语言回答。
- 需要展示代码时使用 Markdown 代码块，并标注语言。
- 解释、步骤、对比使用 Markdown 列表 / 强调等结构化呈现。
- 不确定时如实说明，不编造事实。
"""

CAT_BANNER = r"""  /\_/\
 ( o.o )
  > ^ <"""


def render_banner(version: str, cwd: str) -> str:
    """拼出启动横幅：猫 + 应用名与版本 + 工作目录 + 就绪提示行。"""
    lines = [
        CAT_BANNER,
        f"  koyoCode v{version}",
        f"  cwd: {cwd}",
        "",
        "  ● 就绪：输入消息后 Enter 发送 · Alt+Enter 换行 · /exit 或 Ctrl+C 退出",
        "",
    ]
    return "\n".join(lines)
