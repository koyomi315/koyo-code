"""配置加载与映射：``Settings`` YAML、``load_settings``、``to_rule_set``、
友好名映射、类别判定与路径提取。

三层配置文件（user/project/local）共享 ``Settings`` 结构；``load_settings`` 文件缺失→
空 ``Settings`` 不抛、解析失败→抛 ``SettingsError``（调用方降级，N5）。

``classify`` 系列：

- ``friendly_name``：bash→Bash, read_file→Read, ... 未知原样。
- ``categorize(internal, read_only)``：``read_only`` 优先→``READ``；否则 write_file/
  edit_file→``WRITE``；其余（含 bash、未知工具）→``EXEC``（N7 最严）。
- ``extract_target(call)``：``json.loads(call.input)`` 取字段；返回
  ``(target, is_file, ok)``，``ok=False`` 表示解析失败或缺必填字段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from koyocode.llm import ToolUseBlock
from koyocode.permission import Category
from koyocode.permission.rule import Rule, RuleSet, parse_rule

__all__ = [
    "PermissionsBlock",
    "Settings",
    "SettingsError",
    "categorize",
    "extract_target",
    "friendly_name",
    "load_settings",
    "to_rule_set",
]


class SettingsError(Exception):
    """配置解析错误（调用方降级跳过该文件，N5）。"""


# 内部名 -> 友好名映射（与 Claude Code 习惯一致，规则更可读）。
_FRIENDLY: dict[str, str] = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}

# 文件类工具（取 path 字段、is_file=True）。
_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "glob", "grep"})
# 写类工具（write_file/edit_file -> WRITE，read_only=False 时）。
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


@dataclass
class PermissionsBlock:
    """单文件 permissions 块：allow/deny 规则串列表。"""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    """单个 YAML 配置文件结构（F4）。"""

    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    """加载单个配置文件。

    - 文件不存在→空 ``Settings``（不抛）。
    - 读到但解析失败→抛 ``SettingsError``（调用方降级）。
    - 结构异常（非 dict）→抛 ``SettingsError``。
    """
    p = Path(path)
    if not p.exists():
        return Settings()
    try:
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise SettingsError(f"{path}: YAML 解析失败: {e}") from e
    except OSError as e:
        raise SettingsError(f"{path}: 读取失败: {e}") from e
    return _from_dict(data)


def _from_dict(data: object) -> Settings:
    """从已解析的 YAML 对象构造 ``Settings``；结构异常抛 ``SettingsError``。"""
    if data is None:
        return Settings()
    if not isinstance(data, dict):
        raise SettingsError(f"配置应为映射，得到 {type(data).__name__}")
    default_mode = data.get("default_mode", "")
    if default_mode is not None and not isinstance(default_mode, str):
        raise SettingsError("default_mode 应为字符串")
    perms_raw = data.get("permissions") or {}
    if not isinstance(perms_raw, dict):
        raise SettingsError("permissions 应为映射")
    allow = perms_raw.get("allow") or []
    deny = perms_raw.get("deny") or []
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise SettingsError("permissions.allow/deny 应为列表")
    return Settings(
        default_mode=default_mode or "",
        permissions=PermissionsBlock(allow=list(allow), deny=list(deny)),
    )


def to_rule_set(s: Settings) -> RuleSet:
    """``Settings`` -> ``RuleSet``：allow/deny 各条 ``parse_rule``，非法条目跳过（N5）。"""
    allow_rules: list[Rule] = []
    for line in s.permissions.allow:
        rule, ok = parse_rule(line)
        if ok:
            allow_rules.append(Rule(tool=rule.tool, pattern=rule.pattern, allow=True))
    deny_rules: list[Rule] = []
    for line in s.permissions.deny:
        rule, ok = parse_rule(line)
        if ok:
            deny_rules.append(Rule(tool=rule.tool, pattern=rule.pattern, allow=False))
    return RuleSet(allow=allow_rules, deny=deny_rules)


def friendly_name(internal: str) -> str:
    """内部工具名 -> 友好名；未知原样返回。"""
    return _FRIENDLY.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    """判定工具类别。

    - ``read_only`` 优先（哪怕未知工具，只要声明只读即 ``READ``）。
    - 否则 write_file/edit_file→``WRITE``；其余（含 bash、未知）→``EXEC``（N7 最严）。
    """
    if read_only:
        return Category.READ
    if internal in _WRITE_TOOLS:
        return Category.WRITE
    return Category.EXEC


def _parse_input(call: ToolUseBlock) -> tuple[dict | None, bool]:
    """解析 ``call.input`` 为 dict；返回 ``(data, ok)``，``ok=False`` 表示非对象或解析失败。"""
    raw = call.input
    if isinstance(raw, dict):
        return raw, True
    if not isinstance(raw, str):
        return None, False
    if not raw.strip():
        return {}, True
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    return data, True


def extract_target(call: ToolUseBlock) -> tuple[str, bool, bool]:
    """提取工具调用目标与形态。

    返回 ``(target, is_file, ok)``：

    - 文件类（read_file/write_file/edit_file）：取 ``path``，``is_file=True``；
      缺 path / 解析失败 → ``(path-or-"", True, False)``。
    - 搜索类（glob/grep）：取 ``path``（搜索根目录，空→``"."``），``is_file=True``；
      缺/失败 → ``("", True, False)``。
    - bash：取 ``command``，``is_file=False``；缺/失败 → ``("", False, False)``。
    - 未知工具 → ``("", False, False)``。
    """
    name = call.name
    data, parsed = _parse_input(call)
    if not parsed:
        data = None
    if name in _FILE_TOOLS:
        if data is None:
            return "", True, False
        path = data.get("path")
        if not isinstance(path, str) or not path:
            # glob/grep 空 path 视为 "."（搜索根默认当前目录）；其余缺 path 视为失败
            if name in ("glob", "grep") and (path is None or path == ""):
                return ".", True, True
            return "", True, False
        return path, True, True
    if name == "bash":
        if data is None:
            return "", False, False
        cmd = data.get("command")
        if not isinstance(cmd, str):
            return "", False, False
        return cmd, False, True
    # 未知工具：取不到 target，按 EXEC 类兜底
    return "", False, False
