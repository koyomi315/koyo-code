"""config 模块单测。"""

from pathlib import Path

import pytest

from koyocode.config import Config, ConfigError, ProviderConfig, load


def _write(tmp_path: Path, text: str) -> str:
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    return str(f)


def test_load_valid_anthropic(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: Claude
    protocol: anthropic
    api_key: sk-xxx
    model: claude-3-5-sonnet-latest
    thinking: true
""",
    )
    cfg = load(path)
    assert isinstance(cfg, Config)
    assert len(cfg.providers) == 1
    p = cfg.providers[0]
    assert isinstance(p, ProviderConfig)
    assert p.name == "Claude"
    assert p.protocol == "anthropic"
    assert p.api_key == "sk-xxx"
    assert p.model == "claude-3-5-sonnet-latest"
    assert p.thinking is True
    assert p.base_url is None


def test_load_valid_openai_with_base_url(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: Local
    protocol: openai
    api_key: sk-xxx
    model: gpt-4o
    base_url: http://localhost:11434/v1
""",
    )
    cfg = load(path)
    p = cfg.providers[0]
    assert p.protocol == "openai"
    assert p.base_url == "http://localhost:11434/v1"
    assert p.thinking is False


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不存在"):
        load(str(tmp_path / "nope.yaml"))


def test_missing_api_key(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: X
    protocol: anthropic
    model: claude-3-5-sonnet-latest
""",
    )
    with pytest.raises(ConfigError, match=r"providers\[0\]\.api_key"):
        load(path)


def test_invalid_protocol(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: X
    protocol: gemini
    api_key: k
    model: m
""",
    )
    with pytest.raises(ConfigError, match="protocol"):
        load(path)


def test_empty_providers(tmp_path: Path) -> None:
    path = _write(tmp_path, "providers: []")
    with pytest.raises(ConfigError, match="为空"):
        load(path)


def test_missing_providers_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "foo: bar\n")
    with pytest.raises(ConfigError, match="为空"):
        load(path)


def test_invalid_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, 'name: "unclosed\n')
    with pytest.raises(ConfigError, match="YAML"):
        load(path)


def test_multiple_providers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k1
    model: m1
  - name: B
    protocol: openai
    api_key: k2
    model: m2
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 2
    assert cfg.providers[0].name == "A"
    assert cfg.providers[1].protocol == "openai"


def test_second_provider_missing_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k1
    model: m1
  - name: B
    protocol: openai
    api_key: k2
""",
    )
    with pytest.raises(ConfigError, match=r"providers\[1\]\.model"):
        load(path)
