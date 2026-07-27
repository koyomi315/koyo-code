"""配置加载与映射单测：加载降级、to_rule_set、friendly_name/categorize/extract_target。"""

import json

import pytest

from koyocode.llm import ToolUseBlock
from koyocode.permission import Category
from koyocode.permission.rule import RuleSet
from koyocode.permission.settings import (
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)


def test_load_settings_missing_file_is_empty(tmp_path) -> None:
    s = load_settings(str(tmp_path / "nope.yaml"))
    assert s.default_mode == ""
    assert s.permissions.allow == [] and s.permissions.deny == []


def test_load_settings_bad_yaml_raises(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("default_mode: [unterminated")
    with pytest.raises(SettingsError):
        load_settings(str(p))


def test_load_settings_valid(tmp_path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "default_mode: acceptEdits\n"
        "permissions:\n"
        "  allow: ['Bash(git *)']\n"
        "  deny: ['Read(.env)']\n"
    )
    s = load_settings(str(p))
    assert s.default_mode == "acceptEdits"
    assert s.permissions.allow == ["Bash(git *)"]
    assert s.permissions.deny == ["Read(.env)"]


def test_to_rule_set_skips_invalid() -> None:
    from koyocode.permission.settings import PermissionsBlock, Settings

    s = Settings(permissions=PermissionsBlock(allow=["Bash(git *)", "(", "Read"], deny=[]))
    rs = to_rule_set(s)
    assert len(rs.allow) == 2
    assert {r.tool for r in rs.allow} == {"Bash", "Read"}


def test_friendly_name() -> None:
    assert friendly_name("bash") == "Bash"
    assert friendly_name("read_file") == "Read"
    assert friendly_name("glob") == "Glob"
    assert friendly_name("unknown_x") == "unknown_x"


def test_categorize() -> None:
    assert categorize("read_file", True) == Category.READ
    assert categorize("glob", True) == Category.READ
    assert categorize("write_file", False) == Category.WRITE
    assert categorize("edit_file", False) == Category.WRITE
    assert categorize("bash", False) == Category.EXEC
    # 未知工具：read_only 优先 -> READ；否则最严 EXEC
    assert categorize("ghost", True) == Category.READ
    assert categorize("ghost", False) == Category.EXEC
    # read_only 优于名字：write_file 声明只读 -> READ
    assert categorize("write_file", True) == Category.READ


def test_extract_target_file() -> None:
    c = ToolUseBlock(id="1", name="read_file", input=json.dumps({"path": "/a/b.py"}))
    assert extract_target(c) == ("/a/b.py", True, True)


def test_extract_target_write_missing_path() -> None:
    c = ToolUseBlock(id="1", name="write_file", input=json.dumps({"content": "x"}))
    target, is_file, ok = extract_target(c)
    assert is_file is True and ok is False


def test_extract_target_glob_empty_path_defaults_dot() -> None:
    c = ToolUseBlock(id="1", name="glob", input=json.dumps({"pattern": "**/*.py"}))
    assert extract_target(c) == (".", True, True)


def test_extract_target_bash() -> None:
    c = ToolUseBlock(id="1", name="bash", input=json.dumps({"command": "ls -la"}))
    assert extract_target(c) == ("ls -la", False, True)


def test_extract_target_bash_missing_command() -> None:
    c = ToolUseBlock(id="1", name="bash", input=json.dumps({"cwd": "/tmp"}))
    target, is_file, ok = extract_target(c)
    assert is_file is False and ok is False and target == ""


def test_extract_target_bad_json() -> None:
    c = ToolUseBlock(id="1", name="read_file", input="{bad")
    _, is_file, ok = extract_target(c)
    assert is_file is True and ok is False


def test_extract_target_unknown() -> None:
    c = ToolUseBlock(id="1", name="ghost", input=json.dumps({"x": 1}))
    assert extract_target(c) == ("", False, False)


def test_to_rule_set_builds_allow_deny() -> None:
    from koyocode.permission.settings import PermissionsBlock, Settings

    s = Settings(
        permissions=PermissionsBlock(allow=["Bash(git *)"], deny=["Bash(rm *)", "Read(.env)"])
    )
    rs = to_rule_set(s)
    assert isinstance(rs, RuleSet)
    assert len(rs.allow) == 1 and rs.allow[0].allow is True
    assert len(rs.deny) == 2 and rs.deny[0].allow is False
