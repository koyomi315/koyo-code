"""Anthropic system 块序列化守护测试（AC4/F3）。

断言稳定块带 ``cache_control: ephemeral``、环境块不带；二者分属不同内容块。
守护重构回归：缓存断点必须真实打在稳定块上，否则缓存策略失效。
"""

from koyocode.llm.anthropic_provider import _append_reminder_anthropic, _build_anthropic_system


def test_stable_block_has_cache_control() -> None:
    """稳定块带 cache_control: ephemeral 断点。"""
    blocks = _build_anthropic_system("stable text", "env text")
    assert len(blocks) == 2
    stable = blocks[0]
    assert stable["type"] == "text"
    assert stable["text"] == "stable text"
    assert stable["cache_control"] == {"type": "ephemeral"}


def test_environment_block_has_no_cache_control() -> None:
    """环境块不带 cache_control（不进缓存前缀）。"""
    blocks = _build_anthropic_system("stable text", "env text")
    env = blocks[1]
    assert env["type"] == "text"
    assert env["text"] == "env text"
    assert "cache_control" not in env


def test_stable_block_precedes_environment() -> None:
    """稳定块在环境块之前（缓存前缀=工具+稳定块，env 在断点后）。"""
    blocks = _build_anthropic_system("STABLE", "ENV")
    assert blocks[0]["text"] == "STABLE"
    assert blocks[1]["text"] == "ENV"


def test_empty_stable_only_environment() -> None:
    """稳定块为空时只产环境块（无断点）。"""
    blocks = _build_anthropic_system("", "env only")
    assert len(blocks) == 1
    assert blocks[0]["text"] == "env only"
    assert "cache_control" not in blocks[0]


def test_empty_environment_only_stable() -> None:
    """环境块为空时只产稳定块（带断点）。"""
    blocks = _build_anthropic_system("stable only", "")
    assert len(blocks) == 1
    assert blocks[0]["text"] == "stable only"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_both_empty_produces_no_blocks() -> None:
    """两者皆空时不产任何块。"""
    assert _build_anthropic_system("", "") == []


# ───────── reminder 织入消息通道（N3/AC12）─────────
def test_reminder_merged_into_last_user_str_content() -> None:
    """reminder 并入末条 user 文本回合（str content 转块列表后追加，不新起消息）。"""
    messages = [{"role": "user", "content": "hello"}]
    _append_reminder_anthropic(messages, "<system-reminder>x</system-reminder>")
    assert len(messages) == 1
    last = messages[-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    assert last["content"][0] == {"type": "text", "text": "hello"}
    assert last["content"][1] == {"type": "text", "text": "<system-reminder>x</system-reminder>"}


def test_reminder_merged_into_last_user_list_content() -> None:
    """reminder 并入末条 user tool_result 回合（list content 直接追加文本块）。"""
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "r"}]}
    ]
    _append_reminder_anthropic(messages, "REM")
    assert len(messages) == 1
    last = messages[-1]
    assert len(last["content"]) == 2
    assert last["content"][1] == {"type": "text", "text": "REM"}


def test_reminder_new_user_when_last_is_assistant() -> None:
    """末条为 assistant 时新起一条 user 消息承载 reminder（避免连续 user，N3）。"""
    messages = [{"role": "assistant", "content": "ok"}]
    _append_reminder_anthropic(messages, "REM")
    assert len(messages) == 2
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == [{"type": "text", "text": "REM"}]


def test_reminder_new_user_when_messages_empty() -> None:
    """空消息时新起一条 user 消息承载 reminder。"""
    messages: list[dict] = []
    _append_reminder_anthropic(messages, "REM")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
