"""摘要 Prompt 模板与解析。

维护两阶段摘要指令（``<analysis>`` 草稿 + ``<summary>`` 正式 9 部分摘要）的全文
文案，把对话序列化嵌入，并从模型返回里抠出 ``<summary>`` 正文。9 个小节标题用
固定字面字符串，便于 ``extract_summary`` 与单测匹配。
"""

from __future__ import annotations

import logging
import re

from koyocode.llm import Message

log = logging.getLogger(__name__)

# 9 个固定小节标题（字面字符串，供 prompt 内嵌与测试匹配）
SUMMARY_SECTIONS = [
    "## 1 主要请求和意图",
    "## 2 关键技术概念",
    "## 3 文件和代码段",
    "## 4 错误和修复",
    "## 5 问题解决过程",
    "## 6 所有用户消息原文",
    "## 7 待办任务",
    "## 8 当前工作",
    "## 9 可能的下一步",
]

SUMMARY_INSTRUCTION = """\
你正在为一段编码 Agent 对话生成结构化摘要。请分两阶段输出。

第一阶段：在 <analysis> 标签内写出分析草稿（梳理对话脉络、关键决策与未决问题），\
这部分会被丢弃，不会进入最终摘要。

第二阶段：在 <summary> 标签内写出正式摘要，必须严格按以下 9 个小节顺序输出，\
每节用给定标题：

## 1 主要请求和意图
## 2 关键技术概念
## 3 文件和代码段
## 4 错误和修复
## 5 问题解决过程
## 6 所有用户消息原文
## 7 待办任务
## 8 当前工作
## 9 可能的下一步

其中第 6 节必须按时间顺序逐条保留本会话中所有用户消息的原文（不得改写、不得省略）。\
第 8 节是全文最详细的一段，需覆盖正在做什么、停在哪一步。

不要调用任何工具，输出纯文本。"""


def serialize_conversation(msgs: list[Message]) -> str:
    """把对话扁平化成可读文本（确定性格式，便于固定预期）。

    - user / assistant 纯文本消息：``role: <content>``
    - assistant 工具调用回合：先输出 preamble 正文，再逐条 ``[call <name> id=<id> args=<json>]``
    - tool 消息内每条 result：``[result id=<id> is_error=<bool>] <content>``
    """
    lines: list[str] = []
    for msg in msgs:
        if msg.tool_results:
            for tr in msg.tool_results:
                lines.append(
                    f"[result id={tr.tool_call_id} is_error={tr.is_error}] {tr.content or ''}"
                )
        else:
            if msg.content:
                lines.append(f"{msg.role}: {msg.content}")
            for tu in msg.tool_uses:
                lines.append(f"[call {tu.name} id={tu.id} args={tu.input}]")
    return "\n".join(lines)


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    """构造摘要请求体：长度为 1 的列表，仅一条 user 消息。

    content 为 ``SUMMARY_INSTRUCTION`` + 序列化对话，嵌入 ``[conversation]`` 标记后。
    """
    serialized = serialize_conversation(msgs)
    content = f"{SUMMARY_INSTRUCTION}\n\n[conversation]\n{serialized}"
    return [Message(role="user", content=content)]


def extract_summary(raw: str) -> str:
    """从模型返回文本里抠出最后一对 ``<summary>...</summary>`` 之间的正文。

    ``<analysis>`` 部分直接丢弃。提取失败时返回原文并记 warning，让上层降级使用。
    """
    matches = re.findall(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if matches:
        return matches[-1].strip()
    log.warning("摘要返回中未找到 <summary> 标签，降级使用原文")
    return raw
