"""规则与匹配：``Rule``/``RuleSet``、``parse_rule``、``match_pattern``（glob）。

匹配语义（见 plan）：

- ``pattern == ""``：恒匹配（匹配该工具全部调用）。
- **命令 glob**（``Bash``）：``*`` 匹配任意字符（含空格）、其余字面；``**`` 等价 ``*``。
  整串 ``re.fullmatch``。
- **文件路径 glob**（``Read``/``Write``/``Edit``/``Glob``/``Grep`` 等）：按 ``/`` 分段，
  ``*`` 段内匹配（不跨段）、``**`` 跨段匹配任意层级；目标为项目相对 slash 路径。
  参照 ``tool/glob.py`` 的 ``match_segments`` 思路。

规则优先级：同层 ``deny`` 先于 ``allow``（``RuleSet.match`` 先遍历 deny 再 allow）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from koyocode.permission import Decision

__all__ = ["Rule", "RuleSet", "match_pattern", "parse_rule"]

# 命令类工具的友好名（用命令 glob）；其余按文件路径 glob。
_COMMAND_TOOLS = frozenset({"Bash"})


@dataclass
class Rule:
    """单条权限规则。

    - ``tool``：友好名（Bash/Read/Write/Edit/Glob/Grep）。
    - ``pattern``：模式段；``""`` 表示匹配该工具全部调用。
    - ``allow``：``True``=allow，``False``=deny。
    """

    tool: str
    pattern: str
    allow: bool


@dataclass
class RuleSet:
    """一组 allow/deny 规则；``match`` 先 deny 再 allow、就近命中即返回。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """返回 ``(Decision, hit)``：先遍历 deny 命中→``(DENY, True)``，再 allow→
        ``(ALLOW, True)``；均不命中→``(ALLOW, False)``（第二个 bool 表示是否命中规则）。"""
        for r in self.deny:
            if r.tool == friendly and match_pattern(r.pattern, target, friendly):
                return Decision.DENY, True
        for r in self.allow:
            if r.tool == friendly and match_pattern(r.pattern, target, friendly):
                return Decision.ALLOW, True
        return Decision.ALLOW, False


def _glob_to_regex(segment: str) -> str:
    """单段 glob -> 正则：``*`` 匹配段内任意字符（不含 ``/``），其余逐字转义。"""
    out: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        c = segment[i]
        if c == "*":
            out.append("[^/]*")
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _match_path_glob(pattern: str, target: str) -> bool:
    """文件路径 glob：按 ``/`` 分段，``**`` 跨段、``*`` 段内。"""
    pat_segs = pattern.split("/")
    tgt_segs = target.split("/")
    return _match_segments(pat_segs, tgt_segs)


def _match_segments(pat_segs: list[str], tgt_segs: list[str]) -> bool:
    """递归段匹配（仿 glob match_segments）：``**`` 吃掉任意（含 0）层段。"""
    # 末段 ``**`` 可匹配剩余任意层（含 0）
    if not pat_segs:
        return not tgt_segs
    if pat_segs[0] == "**":
        if len(pat_segs) == 1:
            return True
        # ``**`` 匹配 0..len(tgt) 层，后续段对齐
        for k in range(len(tgt_segs) + 1):
            if _match_segments(pat_segs[1:], tgt_segs[k:]):
                return True
        return False
    if not tgt_segs:
        return False
    seg_re = _glob_to_regex(pat_segs[0])
    if not re.fullmatch(seg_re, tgt_segs[0]):
        return False
    return _match_segments(pat_segs[1:], tgt_segs[1:])


def _match_command_glob(pattern: str, target: str) -> bool:
    """命令 glob：``*``/``**`` 匹配任意字符（含空格），整串 fullmatch。"""
    out: list[str] = []
    for c in pattern:
        if c == "*":
            out.append(".*")
        else:
            out.append(re.escape(c))
    return re.fullmatch("".join(out), target) is not None


def match_pattern(pattern: str, target: str, friendly: str = "") -> bool:  # noqa: ARG001
    """``pattern`` 是否匹配 ``target``。

    ``friendly`` 为空或非命令类时按文件路径 glob 匹配；``friendly`` 属于命令类
    （``Bash``）时按命令 glob 匹配。``pattern == ""`` 恒匹配。
    """
    if pattern == "":
        return True
    if friendly in _COMMAND_TOOLS:
        return _match_command_glob(pattern, target)
    return _match_path_glob(pattern, target)


def parse_rule(s: str) -> tuple[Rule, bool]:
    """解析 ``Tool(pattern)`` 或 ``Tool``；非法返回 ``(_EMPTY, False)``。

    括号内模式可含空格/``*``/``**``；括号不配对或为空返回失败。
    """
    if not isinstance(s, str):
        return Rule("", "", False), False
    s = s.strip()
    if not s:
        return Rule("", "", False), False
    idx = s.find("(")
    if idx == -1:
        # 无括号：整串为工具名、空模式（全匹配）
        tool = s.strip()
        if not tool or " " in tool or "(" in tool or ")" in tool:
            return Rule("", "", False), False
        return Rule(tool, "", True), True
    # 必须以 ')' 结尾且括号正确闭合
    if not s.endswith(")"):
        return Rule("", "", False), False
    tool = s[:idx].strip()
    inner = s[idx + 1 : -1]
    if not tool or " " in tool:
        return Rule("", "", False), False
    inner = inner.strip()
    # 不允许内嵌括号（简单规则语法）
    if "(" in inner or ")" in inner:
        return Rule("", "", False), False
    # allow 形态：parse_rule 不在此判 allow/deny，默认 allow=True；调用方据来源赋 allow。
    return Rule(tool, inner, True), True