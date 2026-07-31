"""配置加载与校验：从 YAML 读取 providers 列表。

配置文件结构见 ``.koyocode/config.yaml.example``。任一校验失败抛出
``ConfigError``，携带可读信息（指明哪个 provider 的哪个字段）。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from koyocode.config.protocol_defaults import (
    DEFAULT_ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_OPENAI_CONTEXT_WINDOW,
)


class ConfigError(Exception):
    """配置加载或校验失败。"""


@dataclass
class ProviderConfig:
    """单个 provider 的配置。"""

    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None  # None 则用 SDK 默认端点
    thinking: bool = False  # 仅 anthropic 生效
    context_window: int = 0  # 单位 token，0 表示走协议默认


@dataclass
class Config:
    """整体配置：providers 列表。"""

    providers: list[ProviderConfig] = field(default_factory=list)


def effective_context_window(p: ProviderConfig) -> int:
    """返回 provider 的有效上下文窗口：配置 > 0 用配置值，否则按协议默认。

    未知协议保守取 anthropic 默认值（200000）。
    """
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "anthropic":
        return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW


_VALID_PROTOCOLS = {"anthropic", "openai"}


def load(path: str) -> Config:
    """读取并校验配置文件，返回 ``Config``。失败抛 ``ConfigError``。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e
    return _from_dict(raw, path)


def _from_dict(raw: object, path: str) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件根节点应为映射表（dict），实际为 {type(raw).__name__}: {path}")
    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ConfigError("providers 列表为空或缺失")
    providers = [_parse_provider(item, i) for i, item in enumerate(providers_raw)]
    return Config(providers=providers)


def _parse_provider(item: object, index: int) -> ProviderConfig:
    prefix = f"providers[{index}]"
    if not isinstance(item, dict):
        raise ConfigError(f"{prefix} 应为映射表（dict）")

    name = item.get("name")
    protocol = item.get("protocol")
    api_key = item.get("api_key")
    model = item.get("model")
    base_url = item.get("base_url")
    thinking = item.get("thinking", False)
    context_window = item.get("context_window", 0)

    _require_non_empty(name, f"{prefix}.name")
    _require_non_empty(protocol, f"{prefix}.protocol")
    _require_non_empty(api_key, f"{prefix}.api_key")
    _require_non_empty(model, f"{prefix}.model")
    if protocol not in _VALID_PROTOCOLS:
        raise ConfigError(f"{prefix}.protocol 非法: {protocol!r}，应为 'anthropic' 或 'openai'")
    if base_url is not None and not isinstance(base_url, str):
        raise ConfigError(f"{prefix}.base_url 应为字符串")
    if not isinstance(thinking, bool):
        raise ConfigError(f"{prefix}.thinking 应为布尔值")
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window < 0
    ):
        raise ConfigError(f"{prefix}.context_window 应为非负整数")

    return ProviderConfig(
        name=str(name),
        protocol=protocol,  # type: ignore[arg-type]
        api_key=str(api_key),
        model=str(model),
        base_url=base_url or None,
        thinking=thinking,
        context_window=context_window,
    )


def _require_non_empty(value: object, field_name: str) -> None:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ConfigError(f"{field_name} 不能为空")
