"""权限引擎与前四层流水线（F6，短路）。

``Engine.check`` 依次执行四层：

1. **黑名单**（仅 EXEC 类、target 非空）→ 命中 ``Deny``（N1，bypass 也拦）。
2. **沙箱**（仅文件类 ``is_file``）→ 不可解析 → ``Deny``；越界 → ``Deny``（N2）。
3. **规则引擎**（三级 ``local→project→user``，就近命中即返回）→ allow/``Allow``、deny/``Deny``。
4. **模式兜底**（``mode_fallback``）→ ``Allow`` 或 ``Ask``（只产 Allow/Ask，无 Deny 档）。

``new_engine`` 构造：解析项目根、加载三层配置、编译黑名单、确定启动模式；即使
``resolve_root`` 失败也返回非 None 的"空规则安全引擎" + err（cli 注入永不为 None）。
单个配置文件读/解析失败仅降级跳过该文件，绝不向上抛致命异常（N5）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from koyocode.llm import ToolCall
from koyocode.permission import Category, Decision, Mode, parse_mode
from koyocode.permission.blacklist import hits_blacklist
from koyocode.permission.rule import RuleSet
from koyocode.permission.sandbox import relative_to_root, resolve_root, sandbox_ok
from koyocode.permission.settings import (
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)

__all__ = ["Engine", "mode_fallback", "new_engine"]


@dataclass
class Engine:
    """权限引擎：持有项目根、黑名单、三级规则集与启动模式。"""

    root: str
    blacklist: list[re.Pattern[str]] = field(default_factory=list)
    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)
    local_path: str = ""
    _start_mode: Mode = Mode.DEFAULT

    def start_mode(self) -> Mode:
        """启动默认模式（取自配置，皆无→``Mode.DEFAULT``）。"""
        return self._start_mode

    def persist_local_allow(self, call: ToolCall) -> None:
        """永久放行：精确 allow 规则写入本地层文件并同步内存（异常向上抛，agent 侧记日志）。"""
        from koyocode.permission.persist import persist_local_allow as _do

        _do(self, call)

    def check(self, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]:
        """前四层判定（agent 每次执行工具前调用）；返回 ``(裁决, 原因)``。

        ``read_only`` 由调用方按批类型给定（等价 ``registry.is_read_only``）。
        原因文案统一，供 Deny 回灌与 Ask 展示一致（见 plan「reason 来源表」）。
        """
        cat = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        target, is_file, ok = extract_target(call)

        # ① 黑名单（仅 EXEC 类、target 非空）：bypass 也拦
        if cat == Category.EXEC and target != "" and hits_blacklist(target):
            return Decision.DENY, f"命中危险命令黑名单：{target}"

        # ② 沙箱（仅文件类）
        if is_file:
            if not ok:
                return Decision.DENY, "无法解析文件路径参数，安全拒绝"
            if not sandbox_ok(self, target):
                return Decision.DENY, f"路径在项目目录之外：{target}"

        # ③ 规则引擎：local → project → user，就近命中即返回
        match_target = relative_to_root(self.root, target) if is_file else target
        for rs in (self.local, self.project, self.user):
            d, hit = rs.match(friendly, match_target)
            if hit:
                if d == Decision.DENY:
                    return Decision.DENY, f"匹配 deny 规则：{friendly}({target})"
                return Decision.ALLOW, f"匹配 allow 规则：{friendly}({target})"

        # ④ 模式兜底
        fb = mode_fallback(mode, cat)
        if fb == Decision.ALLOW:
            return Decision.ALLOW, ""
        return Decision.ASK, f"{mode} 模式下 {friendly}({target}) 类操作需确认"


def mode_fallback(mode: Mode, cat: Category) -> Decision:
    """F5 模式兜底矩阵；只产 ``Allow`` / ``Ask``（无 Deny 档）。"""
    if cat == Category.READ or mode == Mode.BYPASS:
        return Decision.ALLOW
    if mode == Mode.ACCEPT_EDITS and cat == Category.WRITE:
        return Decision.ALLOW
    return Decision.ASK


def _load_layer(path: str) -> tuple[RuleSet, str]:
    """加载单层配置：解析失败降级为空 RuleSet（不抛）；返回 ``(rule_set, default_mode)``。"""
    try:
        s = load_settings(path)
    except SettingsError:
        return RuleSet(), ""
    return to_rule_set(s), s.default_mode


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    """构造引擎；``resolve_root`` 失败时返回空规则安全引擎 + err，其余配置错仅降级。

    三层配置文件：user=``~/.koyocode/settings.yaml``、project=``<root>/.koyocode/settings.yaml``、
    local=``<root>/.koyocode/settings.local.yaml``。``start_mode`` 依次取 local/project/user 的
    ``default_mode``（``parse_mode`` 成功者，local 优先），皆无→``Mode.DEFAULT``。
    """
    err: Exception | None = None
    try:
        resolved = resolve_root(root)
    except Exception as e:  # noqa: BLE001 - 致命错也降级为空引擎 + err
        resolved = root
        err = e

    local_path = str(Path(resolved) / ".koyocode" / "settings.local.yaml")

    try:
        user_path = str(Path.home() / ".koyocode" / "settings.yaml")
    except Exception:  # noqa: BLE001 - home 不可解析时跳过 user 层
        user_path = ""
    project_path = str(Path(resolved) / ".koyocode" / "settings.yaml")

    user_rs, user_mode = _load_layer(user_path)
    project_rs, project_mode = _load_layer(project_path)
    local_rs, local_mode = _load_layer(local_path)

    # start_mode：local > project > user，parse_mode 成功者优先
    start_mode = Mode.DEFAULT
    chosen = False
    for m in (local_mode, project_mode, user_mode):
        if m:
            mode, ok = parse_mode(m)
            if ok:
                start_mode = mode
                chosen = True
                break
    _ = chosen  # 仅用于可读性

    engine = Engine(
        root=resolved,
        user=user_rs,
        project=project_rs,
        local=local_rs,
        local_path=local_path,
        _start_mode=start_mode,
    )
    return engine, err
