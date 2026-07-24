"""规则解析与匹配单测：parse_rule / match_pattern / RuleSet.match 优先级。"""

from koyocode.permission import Decision
from koyocode.permission.rule import Rule, RuleSet, match_pattern, parse_rule


def test_parse_rule_with_pattern() -> None:
    r, ok = parse_rule("Bash(git *)")
    assert ok and r.tool == "Bash" and r.pattern == "git *"


def test_parse_rule_no_pattern() -> None:
    r, ok = parse_rule("Read")
    assert ok and r.tool == "Read" and r.pattern == ""


def test_parse_rule_invalid() -> None:
    assert parse_rule("")[1] is False
    assert parse_rule("(git *)")[1] is False  # 缺工具名
    assert parse_rule("Bash(git *")[1] is False  # 括号不配对
    assert parse_rule("Bash()(x)")[1] is False  # 内嵌括号


def test_match_pattern_empty_always() -> None:
    assert match_pattern("", "anything")
    assert match_pattern("", "")


def test_match_pattern_command_glob() -> None:
    assert match_pattern("git *", "git status", "Bash") is True
    assert match_pattern("git *", "npm i", "Bash") is False
    assert match_pattern("pytest", "pytest", "Bash") is True


def test_match_pattern_path_glob() -> None:
    assert match_pattern("src/**", "src/a/b.py") is True
    assert match_pattern("src/**", "docs/x") is False
    assert match_pattern("*.py", "a.py") is True
    assert match_pattern("*.py", "sub/a.py") is False  # * 不跨段


def test_match_pattern_escaped_glob_is_literal() -> None:
    """反斜杠转义的字面星号不应被当成通配（精确规则不被泛化）。"""
    # 命令：含字面 * 的精确命令命中，含其它前后的串不命中
    assert match_pattern("git push \\* origin", "git push * origin", "Bash") is True
    assert match_pattern("git push \\* origin", "git push x origin", "Bash") is False
    # 路径：转义星号作为字面文件名
    assert match_pattern("a\\*b.py", "a*b.py") is True
    assert match_pattern("a\\*b.py", "axxb.py") is False


def test_ruleset_deny_priority_over_allow() -> None:
    rs = RuleSet(
        allow=[Rule("Bash", "git *", allow=True)],
        deny=[Rule("Bash", "git *", allow=False)],
    )
    d, hit = rs.match("Bash", "git status")
    assert d == Decision.DENY and hit is True


def test_ruleset_allow_then_miss() -> None:
    rs = RuleSet(allow=[Rule("Bash", "git *", allow=True)])
    assert rs.match("Bash", "git status") == (Decision.ALLOW, True)
    assert rs.match("Bash", "npm i") == (Decision.ALLOW, False)


def test_ruleset_other_tool_miss() -> None:
    rs = RuleSet(deny=[Rule("Bash", "rm *", allow=False)])
    # Read 规则不存在 -> 不命中
    assert rs.match("Read", "x.txt") == (Decision.ALLOW, False)
