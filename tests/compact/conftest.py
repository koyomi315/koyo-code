"""compact 包测试公用 fixture。"""

from __future__ import annotations

import pytest

from koyocode.llm import StreamEvent


class FakeCompactProvider:
    """脚本化 provider：按调用序号返回预设帧序列。

    - ``scripts``: ``list[list[StreamEvent]]``，第 i 次 ``stream`` 调用返回第 i 帧列表；
      超出范围时按 ``default_summary`` 决定回退（默认返回一段 ``<summary>`` 文本）。
    - ``stream_calls`` / ``summarize_calls``：分别计数全部调用与摘要请求（tools 为空）。
    - ``requests``：记录每次请求的 ``Request``，供测试断言 messages / tools。
    """

    def __init__(self, scripts=None, default_summary=True):
        self._scripts = scripts or []
        self._default_summary = default_summary
        self.stream_calls = 0
        self.summarize_calls = 0
        self.requests = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake"

    async def stream(self, req):
        self.stream_calls += 1
        self.requests.append(req)
        if not req.tools:
            self.summarize_calls += 1
        idx = self.stream_calls - 1
        if idx < len(self._scripts):
            frames = self._scripts[idx]
        elif self._default_summary:
            frames = [StreamEvent(text="<summary>ok</summary>", done=True)]
        else:
            frames = [StreamEvent(done=True)]
        for f in frames:
            yield f


@pytest.fixture
def fake_provider() -> FakeCompactProvider:
    return FakeCompactProvider()


@pytest.fixture
def make_fake_provider():
    """工厂 fixture：按需构造带预设脚本的 FakeCompactProvider。"""

    def _make(scripts=None, default_summary=True):
        return FakeCompactProvider(scripts=scripts, default_summary=default_summary)

    return _make
