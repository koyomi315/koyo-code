"""永久放行规则写入（``rule_for`` 生成精确规则 + ``Engine.persist_local_allow``）。

- ``rule_for(call)`` 据工具调用生成**精确**规则（不含通配）：命令/路径均经 ``escape_glob``
  转义字面 glob 元字符（``*``/``?``），防止规则的匹配被泛化。返回 ``(内存 Rule, YAML 串, ok)``。
- ``Engine.persist_local_allow(call)`` 追加规则到本地层 ``settings.local.yaml`` 的
  ``permissions.allow``（去重）并 ``yaml.safe_dump`` 重写、同步内存在 ``self.local.allow``。
  异常向上抛，调用方捕获后只记日志不阻断。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from koyocode.llm import ToolUseBlock
from koyocode.permission.rule import Rule
from koyocode.permission.sandbox import relative_to_root
from koyocode.permission.settings import (
    SettingsError,
    extract_target,
    friendly_name,
    load_settings,
)

__all__ = ["persist_local_allow", "rule_for"]

# glob 元字符（命令与路径 glob 通用）
_GLOB_META = {"*": "\\*", "?": "\\?"}


def escape_glob(s: str) -> str:
    """转义字面 glob 元字符 ``*``/``?``，防止精确规则被串匹配泛化。"""
    out: list[str] = []
    for c in s:
        out.append(_GLOB_META.get(c, c))
    return "".join(out)


def rule_for(call: ToolUseBlock, root: str = "") -> tuple[Rule, str, bool]:
    """为单次放行生成精确规则（内存 Rule + YAML 串）。

    返回 ``(rule, yaml_str, ok)``：``ok=False`` 表示解析失败 / 未知工具。

    文件类目标的模式经 ``relative_to_root`` 规整为项目相对路径，与 ``Engine.check``
    匹配时的规整一致--保证选「永久」后、重载引擎对同调用（含绝对路径）仍判放行。
    """
    target, is_file, ok = extract_target(call)
    friendly = friendly_name(call.name)
    known = call.name in {"bash", "read_file", "write_file", "edit_file", "glob", "grep"}
    if not known or not ok or target == "":
        return Rule("", "", False), "", False

    if is_file:
        pattern = escape_glob(relative_to_root(root, target)) if root else escape_glob(target)
    else:
        pattern = escape_glob(target)
    rule = Rule(tool=friendly, pattern=pattern, allow=True)
    yaml_str = f"{friendly}({pattern})"
    return rule, yaml_str, True


def persist_local_allow(engine: object, call: ToolUseBlock) -> None:
    """把精确 allow 规则写入引擎的本地层文件 + 同步内存（异常向上抛，调用方记日志）。"""
    path = getattr(engine, "local_path", "")
    root = getattr(engine, "root", "")
    if not path:
        return

    rule, yaml_str, ok = rule_for(call, root)
    if not ok:
        return

    # 加载现有（缺失或坏默认空），追加去重，重写
    try:
        settings = load_settings(path)
    except SettingsError:
        from koyocode.permission.settings import PermissionsBlock, Settings

        settings = Settings(permissions=PermissionsBlock())

    allow = list(settings.permissions.allow)
    if yaml_str not in allow:
        allow.append(yaml_str)
    settings.permissions.allow = allow

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "default_mode": settings.default_mode,
        "permissions": {
            "allow": settings.permissions.allow,
            "deny": settings.permissions.deny,
        },
    }
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

    # 同步内存：本地层 allow 追加去重
    local_rs = getattr(engine, "local", None)
    if local_rs is not None and not any(
        r.tool == rule.tool and r.pattern == rule.pattern and r.allow for r in local_rs.allow
    ):
        local_rs.allow.append(rule)
