"""layer1.py 落盘与预览单测。"""

from __future__ import annotations

from pathlib import Path

from koyocode.compact.layer1 import offload_and_snip, spill_single
from koyocode.compact.state import ContentReplacementState, new_session_context
from koyocode.llm import ROLE_TOOL, Message, ToolResultBlock


def test_spill_single_idempotent(tmp_path):
    session = new_session_context(str(tmp_path))
    spill_single(session, "id1", "content")
    p = Path(session.spill_dir) / "id1"
    m1 = p.stat().st_mtime_ns
    spill_single(session, "id1", "content")  # 幂等：不重写
    assert p.stat().st_mtime_ns == m1
    assert p.read_bytes() == b"content"


def test_offload_single_result(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    big = "x" * 60000
    msgs = [Message(role=ROLE_TOOL, tool_results=[ToolResultBlock(tool_call_id="t1", content=big)])]
    out = offload_and_snip(msgs, state, session)
    preview = out[0].tool_results[0].content
    assert preview != big
    assert "original size: 60000 bytes" in preview
    assert "[saved to]" in preview
    assert "[head preview]" in preview
    assert "文件读取工具" in preview
    assert "不要凭头部预览猜测" in preview
    # 头部预览 <= 20 行且 <= 2048 字节
    head = preview.split("[head preview]\n", 1)[1].split("\n完整内容已保存", 1)[0]
    assert head.count("\n") < 20
    assert len(head.encode("utf-8")) <= 2048
    # 落盘文件存在且大小正确
    assert (Path(session.spill_dir) / "t1").stat().st_size == 60000


def test_offload_aggregate(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    big80 = "y" * 80000
    msgs = [
        Message(
            role=ROLE_TOOL,
            tool_results=[
                ToolResultBlock(tool_call_id="a1", content=big80),
                ToolResultBlock(tool_call_id="a2", content=big80),
                ToolResultBlock(tool_call_id="a3", content=big80),
            ],
        )
    ]
    out = offload_and_snip(msgs, state, session)
    replaced = sum(1 for tr in out[0].tool_results if "[content offloaded]" in tr.content)
    assert replaced >= 2
    agg = sum(
        len((tr.content or "").encode("utf-8"))
        for tr in out[0].tool_results
        if "[content offloaded]" not in tr.content
    )
    assert agg <= 200000


def test_offload_decision_freeze(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    big = "x" * 60000
    msgs = [Message(role=ROLE_TOOL, tool_results=[ToolResultBlock(tool_call_id="t1", content=big)])]
    out1 = offload_and_snip(msgs, state, session)
    out2 = offload_and_snip(msgs, state, session)
    assert out2[0].tool_results[0].content == out1[0].tool_results[0].content


def test_offload_spill_failure_retryable(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    big = "x" * 60000
    msgs = [Message(role=ROLE_TOOL, tool_results=[ToolResultBlock(tool_call_id="t1", content=big)])]
    Path(session.spill_dir).chmod(0o500)  # 只读目录，落盘失败
    try:
        out = offload_and_snip(msgs, state, session)
        # 落盘失败：保持原文，账本未写
        assert out[0].tool_results[0].content == big
        assert not state.is_seen("t1")
    finally:
        Path(session.spill_dir).chmod(0o700)


def test_preview_stable_across_rounds(tmp_path):
    session = new_session_context(str(tmp_path))
    state = ContentReplacementState()
    big = "x" * 60000
    msgs = [Message(role=ROLE_TOOL, tool_results=[ToolResultBlock(tool_call_id="t1", content=big)])]
    out1 = offload_and_snip(msgs, state, session)
    out2 = offload_and_snip(msgs, state, session)
    assert out1[0].tool_results[0].content == out2[0].tool_results[0].content
