"""第 1 层预防性压缩：单条 / 聚合落盘 + 决策冻结。

在每轮 LLM 请求组装前执行，是确定性的纯字符串替换，不调用 LLM。把超阈值
工具结果落盘，对话内只留头部预览 + 落盘路径 + 重读提示，从源头压住请求体
膨胀曲线。落盘 -> 改写 content -> 写账本三步通过 ``decide_once`` 在同一临界区
完成，任一步失败则三件都不发生（保持原文 + 不写账本）。
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from koyocode.compact.const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from koyocode.compact.state import ContentReplacementState, SessionContext
from koyocode.llm import Message

log = logging.getLogger(__name__)


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """把单条工具结果内容写入 ``spill_dir/<tool_use_id>``。

    幂等：文件已存在则不重写、不报错。失败抛 ``OSError`` 由上层捕获降级。
    """
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return
    path.write_bytes(content.encode("utf-8"))


def _head_preview(content: str) -> str:
    """取头部预览：先截到 ``PREVIEW_HEAD_LINES`` 行，再按字节截到 ``PREVIEW_HEAD_BYTES``。

    字节级二次截断在 UTF-8 字符边界对齐，避免截出半个字符。
    """
    lines = content.splitlines(keepends=True)[:PREVIEW_HEAD_LINES]
    head = "".join(lines)
    encoded = head.encode("utf-8")
    if len(encoded) <= PREVIEW_HEAD_BYTES:
        return head
    cut = PREVIEW_HEAD_BYTES
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="ignore")


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """构造替换体字符串：原始字节数 + 落盘路径 + 头部预览 + 重读提示。

    固定格式，逐字节稳定输出（同一入参返回完全相等字符串）。
    """
    return "\n".join(
        [
            f"[content offloaded] original size: {original_bytes} bytes",
            f"[saved to] {spill_path}",
            "[head preview]",
            head,
            "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，不要凭头部预览猜测全文",
        ]
    )


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    """遍历 msgs，对每条 ``role=="tool"`` 消息的 ``tool_results`` 做落盘 + 替换。

    单遍扫描 + 候选列表处理（决策只走一次 ``decide_once``）：
      1. 已 Seen 的项通过 ``decide_once`` 取账本存量结果（kept 返回原文、replaced
         复用 ``_replacements[id]``，不重新构造 preview），直接落位；
      2. 未决策项进入候选列表，按 ``content`` 字节倒序处理：
         a. 单条 > ``SINGLE_RESULT_LIMIT`` 必须落盘（F1）；
         b. 否则若当前未落盘聚合字节 > ``MESSAGE_AGGREGATE_LIMIT``，仍按倒序继续
            落盘，直至聚合回落到阈值（F2）；
         c. 未落盘项 kept；
      3. 落盘失败降级为 skip（不写账本，下一轮重试）。

    返回新的 ``list[Message]``，纯函数风格，不修改入参。
    """
    out = copy.deepcopy(msgs)
    for msg in out:
        if msg.role != "tool" or not msg.tool_results:
            continue
        results = msg.tool_results
        # 第 1 步：已 Seen 项落位，未 Seen 项收集为候选
        candidates: list[tuple[int, str, str, int]] = []  # (idx, id, content, nbytes)
        for idx, tr in enumerate(results):
            tid = tr.tool_call_id
            content = tr.content or ""
            if state.is_seen(tid):
                results[idx].content = state.decide_once(tid, content, lambda: ("skip", ""))
            else:
                candidates.append((idx, tid, content, len(content.encode("utf-8"))))
        if not candidates:
            continue
        # 第 2 步：候选按字节倒序
        candidates.sort(key=lambda c: c[3], reverse=True)
        unoffloaded = sum(c[3] for c in candidates)
        # 第 3 步：单条超阈值先落盘（F1），再按聚合预算继续落盘（F2）
        for idx, tid, content, nbytes in candidates:
            must = nbytes > SINGLE_RESULT_LIMIT or unoffloaded > MESSAGE_AGGREGATE_LIMIT
            box: list[str] = [""]

            def decide(
                must: bool = must,
                tid: str = tid,
                content: str = content,
                nbytes: int = nbytes,
                box: list[str] = box,
            ) -> tuple[str, str]:
                if not must:
                    box[0] = "kept"
                    return ("kept", "")
                try:
                    spill_single(session, tid, content)
                except OSError as e:  # noqa: BLE001 - 磁盘问题降级，不阻断对话
                    log.warning("工具结果落盘失败，降级保留原文: %s", e)
                    box[0] = "skip"
                    return ("skip", "")
                spill_path = str(Path(session.spill_dir) / tid)
                box[0] = "replaced"
                return ("replaced", build_preview(nbytes, _head_preview(content), spill_path))

            new_content = state.decide_once(tid, content, decide)
            results[idx].content = new_content
            if box[0] == "replaced":
                unoffloaded -= nbytes
    return out
