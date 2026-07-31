"""provider PTL 错误包装单测（ch08 T20.5）。

模拟 anthropic / openai SDK 抛出上下文过长的 ``BadRequestError``，断言 stream
将其包装为 ``PromptTooLongError`` 并通过 ``StreamEvent.err`` 投递；非 PTL 的 400
错误不被错误包装。
"""

from __future__ import annotations

import httpx
import pytest

from koyocode.config import ProviderConfig
from koyocode.llm import PromptTooLongError, Request, StreamEvent
from koyocode.llm.anthropic_provider import AnthropicProvider
from koyocode.llm.openai_provider import OpenAIProvider


def _cfg(protocol="anthropic"):
    return ProviderConfig(name="t", protocol=protocol, api_key="k", model="m")


def _resp():
    req = httpx.Request("POST", "https://api.example.com/v1")
    return httpx.Response(400, request=req)


async def _collect(provider):  # noqa: ANN001
    events: list[StreamEvent] = []
    async for ev in provider.stream(Request()):
        events.append(ev)
    return events


# ───────── anthropic ─────────


class _FakeAnthropicMessages:
    def __init__(self, err):
        self._err = err

    def stream(self, **params):
        raise self._err


class _FakeAnthropicClient:
    def __init__(self, err):
        self.messages = _FakeAnthropicMessages(err)


@pytest.mark.asyncio
async def test_anthropic_provider_wraps_prompt_too_long(monkeypatch):
    import anthropic

    err = anthropic.BadRequestError("prompt is too long", response=_resp(), body=None)
    client = _FakeAnthropicClient(err)
    monkeypatch.setattr(
        "koyocode.llm.anthropic_provider.anthropic.AsyncAnthropic", lambda **kw: client
    )
    provider = AnthropicProvider(_cfg())
    events = await _collect(provider)

    assert len(events) == 1
    assert isinstance(events[0].err, PromptTooLongError)
    assert events[0].err.__cause__ is err


@pytest.mark.asyncio
async def test_anthropic_provider_does_not_wrap_other_400(monkeypatch):
    import anthropic

    err = anthropic.BadRequestError("invalid model", response=_resp(), body=None)
    client = _FakeAnthropicClient(err)
    monkeypatch.setattr(
        "koyocode.llm.anthropic_provider.anthropic.AsyncAnthropic", lambda **kw: client
    )
    provider = AnthropicProvider(_cfg())
    events = await _collect(provider)

    assert len(events) == 1
    assert not isinstance(events[0].err, PromptTooLongError)


# ───────── openai ─────────


class _FakeOpenAICompletions:
    def __init__(self, err):
        self._err = err

    async def create(self, **kwargs):
        raise self._err


class _FakeOpenAIChat:
    def __init__(self, err):
        self.completions = _FakeOpenAICompletions(err)


class _FakeOpenAIClient:
    def __init__(self, err):
        self.chat = _FakeOpenAIChat(err)


@pytest.mark.asyncio
async def test_openai_provider_wraps_context_length_exceeded(monkeypatch):
    import openai

    err = openai.BadRequestError(
        "This model's maximum context length is 8192 tokens",
        response=_resp(),
        body=None,
    )
    client = _FakeOpenAIClient(err)
    monkeypatch.setattr("koyocode.llm.openai_provider.openai.AsyncOpenAI", lambda **kw: client)
    provider = OpenAIProvider(_cfg("openai"))
    events = await _collect(provider)

    assert len(events) == 1
    assert isinstance(events[0].err, PromptTooLongError)
    assert events[0].err.__cause__ is err


@pytest.mark.asyncio
async def test_openai_provider_does_not_wrap_other_400(monkeypatch):
    import openai

    err = openai.BadRequestError("invalid api key", response=_resp(), body=None)
    client = _FakeOpenAIClient(err)
    monkeypatch.setattr("koyocode.llm.openai_provider.openai.AsyncOpenAI", lambda **kw: client)
    provider = OpenAIProvider(_cfg("openai"))
    events = await _collect(provider)

    assert len(events) == 1
    assert not isinstance(events[0].err, PromptTooLongError)
