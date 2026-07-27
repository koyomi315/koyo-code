"""MCP 配置加载：两层 YAML 合并、``${VAR}`` 展开、字段校验。

从用户级 ``~/.koyocode/config.yaml`` 与项目级 ``<root>/.koyocode.yaml`` 读取
``mcp_servers`` 段，按 server 名合并（项目级同名完整覆盖用户级），对 env /
headers 的值展开 ``${VAR}``，校验后归一化为 :class:`Config`。任何文件缺失或
格式非法均跳过该层并 stderr 告警，绝不抛出（N1）。
"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 ``${VAR}``、已校验）。"""

    type: Literal["stdio", "http"]
    command: str = ""  # stdio 必填
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""  # http 必填
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """mcp_servers 在内存中的归一化形式（已合并）。"""

    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass
class _RawServer:
    """从 YAML 读出的原始 server 定义（未展开、未校验，字段全可选）。"""

    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_file(path: Path) -> dict[str, _RawServer]:
    """读取单个配置文件，返回 server 名 -> ``_RawServer`` 映射。

    文件不存在 -> 空；读 / 解析失败 -> stderr 告警 + 空（降级，不抛出）。
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        print(f"[mcp] warn: load {path} failed: {e}", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        return {}
    servers_raw = raw.get("mcp_servers") or {}
    if not isinstance(servers_raw, dict):
        return {}
    result: dict[str, _RawServer] = {}
    for name, val in servers_raw.items():
        if not isinstance(val, dict):
            continue
        result[str(name)] = _RawServer(
            type=val.get("type"),
            command=val.get("command"),
            args=val.get("args"),
            env=val.get("env"),
            url=val.get("url"),
            headers=val.get("headers"),
        )
    return result


def _expand_vars(s: str) -> tuple[str, list[str]]:
    """展开字符串中的 ``${VAR}``，返回 (展开后字符串, 未定义变量名列表)。"""
    undefined: list[str] = []

    def repl(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in os.environ:
            return os.environ[var]
        undefined.append(var)
        return ""

    expanded = _VAR_RE.sub(repl, s)
    return expanded, undefined


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """对 ``srv.env`` / ``srv.headers`` 的每个值原地展开 ``${VAR}``。

    未定义变量对该 server 限告警一次（局部 set 去重）。
    """
    seen: set[str] = set()
    for mapping in (srv.env, srv.headers):
        if not mapping:
            continue
        for key, value in list(mapping.items()):
            if not isinstance(value, str):
                continue
            expanded, undefined = _expand_vars(value)
            mapping[key] = expanded
            for v in undefined:
                if v not in seen:
                    seen.add(v)
                    print(
                        f"[mcp] warn: undefined env var ${{{v}}} referenced by server {name}",
                        file=sys.stderr,
                    )


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """合并两层：项目级同名 server 整对象覆盖用户级。"""
    merged: dict[str, _RawServer] = {}
    merged.update(user)
    merged.update(project)
    return merged


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """校验单个 server，合法则返回 :class:`ServerConfig`，否则告警并返回 None。"""
    if srv.type not in ("stdio", "http"):
        print(f"[mcp] warn: skip server {name}: invalid type {srv.type!r}", file=sys.stderr)
        return None
    if srv.type == "stdio":
        if not srv.command:
            print(f"[mcp] warn: skip server {name}: stdio missing command", file=sys.stderr)
            return None
    else:  # http
        if not srv.url:
            print(f"[mcp] warn: skip server {name}: http missing url", file=sys.stderr)
            return None
    return ServerConfig(
        type=srv.type,  # type: ignore[arg-type]
        command=srv.command or "",
        args=list(srv.args) if isinstance(srv.args, list) else [],
        env=dict(srv.env) if isinstance(srv.env, dict) else {},
        url=srv.url or "",
        headers=dict(srv.headers) if isinstance(srv.headers, dict) else {},
    )


def load_config(root: str) -> Config:
    """加载并合并两层 MCP 配置，返回归一化的 :class:`Config`（永不抛出）。

    - 用户级：``~/.koyocode/config.yaml``（``Path.home()`` 失败则跳过该层）
    - 项目级：``<root>/.koyocode.yaml``
    - 文件缺失视为空层；格式非法跳过该层 + stderr 告警
    - env / headers 的值展开 ``${VAR}``；command / args / 名字不展开
    - 项目级同名 server 完整覆盖用户级
    """
    # 用户级
    user_servers: dict[str, _RawServer] = {}
    try:
        user_path = Path.home() / ".koyocode" / "config.yaml"
    except (RuntimeError, OSError):
        user_path = None
    if user_path is not None:
        user_servers = _load_file(user_path)

    # 项目级
    project_servers = _load_file(Path(root) / ".koyocode.yaml")

    # 各层展开 ${VAR}（仅 env / headers 的值）
    for sname, srv in user_servers.items():
        _apply_expansion(sname, srv)
    for sname, srv in project_servers.items():
        _apply_expansion(sname, srv)

    # 合并：项目级覆盖用户级同名
    merged = _merge_servers(user_servers, project_servers)

    # 校验
    servers: dict[str, ServerConfig] = {}
    for sname, srv in merged.items():
        validated = _validate_server(sname, srv)
        if validated is not None:
            servers[sname] = validated
    return Config(servers=servers)
