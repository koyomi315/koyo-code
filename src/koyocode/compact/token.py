"""Token 估算。

锚定上一次主对话 provider 返回的真实 usage，对其后新增消息按「字符数 / 3.5」
做增量估算，避免引入精确 tokenizer 的依赖与开销。
"""

from __future__ import annotations

import math

from koyocode.compact.const import ESTIMATE_CHARS_PER_TOKEN
from koyocode.llm import Message, Usage


def usage_anchor(u: Usage) -> int:
    """把 stream 尾事件中的 usage 合并成单一锚点值。

    等价于 ``input_tokens + output_tokens + cache_read + cache_write``。
    锚点语义是「替换」而非「累加」：每次主对话请求结束后用最近一次 usage 覆盖。
    """
    return u.input_tokens + u.output_tokens + u.cache_read + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    """计算一段消息列表的 UTF-8 字节总量。

    累加每条消息的 ``content`` 字节 + 各 ``tool_uses[i].input``（raw JSON 字符串）
    字节 + 各 ``tool_results[i].content`` 字节。字段为 ``None`` 时按 0 计。
    """
    total = 0
    for msg in msgs:
        total += len((msg.content or "").encode("utf-8"))
        for tu in msg.tool_uses:
            total += len((tu.input or "").encode("utf-8"))
        for tr in msg.tool_results:
            total += len((tr.content or "").encode("utf-8"))
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """锚定真实 usage + 锚点之后新增消息的字符增量估算。

    入参语义：
      - ``anchor``：上一次主对话 stream 真实 usage 之和（int）；
      - ``all_msgs``：当前 ``conv.messages()`` 完整列表（须是 layer1 之后的）；
      - ``anchor_msg_len``：anchor 被记录时 ``conv.length()`` 的值，表示已被这份
        usage 算进的消息条数；函数只把 ``all_msgs[anchor_msg_len:]`` 的字符累加，
        避免重复计算历史。

    首轮 / 摘要后（``anchor=0``、``anchor_msg_len=0``）退化为纯字符估算。
    """
    start = max(0, anchor_msg_len)
    tail = all_msgs[start:] if start < len(all_msgs) else []
    return anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)
