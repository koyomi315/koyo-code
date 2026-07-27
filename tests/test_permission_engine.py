"""引擎与前四层流水线单测：逐层短路、跳层、模式矩阵、三级优先级、降级。"""

import json
from pathlib import Path

import pytest

from koyocode.llm import ToolUseBlock
from koyocode.permission import Decision, Mode
from koyocode.permission.engine import Engine, mode_fallback, new_engine
from koyocode.permission.rule import Rule, RuleSet


def _engine_in(tmp_path: Path, **kw) -> Engine:
    e, _ = new_engine(str(tmp_path))
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _read(path: str, call_id: str = "1") -> ToolUseBlock:
    return ToolUseBlock(id=call_id, name="read_file", input=json.dumps({"path": path}))


def _write(path: str, call_id: str = "1") -> ToolUseBlock:
    return ToolUseBlock(
        id=call_id, name="write_file", input=json.dumps({"path": path, "content": "x"})
    )


def _bash(cmd: str, call_id: str = "1") -> ToolUseBlock:
    return ToolUseBlock(id=call_id, name="bash", input=json.dumps({"command": cmd}))


# ── 逐层短路 ──


def test_blacklist_before_sandbox_and_rules(tmp_path: Path) -> None:
    e = _engine_in(tmp_path)
    # rm -rf / 命中黑名单，即便 root 外路径也由黑名单先发
    d, _ = e.check(Mode.DEFAULT, _bash("rm -rf /"), False)
    assert d == Decision.DENY


def test_blacklist_only_exec(tmp_path: Path) -> None:
    """跳层：非 EXEC 不被黑名单拦（read 永不命中黑名单）。"""
    e = _engine_in(tmp_path)
    d, _ = e.check(Mode.DEFAULT, _read("rm -rf /"), True)
    assert d == Decision.ALLOW  # read 类默认 Allow


def test_sandbox_before_rules(tmp_path: Path) -> None:
    e = _engine_in(
        tmp_path,
        local=RuleSet(allow=[Rule("Read", "", allow=True)]),  # 全允许 Read
    )
    # 越界路径先被沙箱拦，不进规则层
    d, _ = e.check(Mode.DEFAULT, _read("/etc/passwd"), True)
    assert d == Decision.DENY


def test_bash_not_blocked_by_sandbox(tmp_path: Path) -> None:
    """跳层：bash 不被沙箱拦（非文件类），从 cwd 外执行的命令仍走规则→模式。"""
    (tmp_path / "inner").mkdir()
    e = _engine_in(tmp_path)
    d, _ = e.check(Mode.DEFAULT, _bash("ls /etc"), False)
    assert d == Decision.ASK  # default exec -> Ask


def test_deny_rule_before_mode(tmp_path: Path) -> None:
    e = _engine_in(tmp_path, local=RuleSet(deny=[Rule("Bash", "rm *", allow=False)]))
    d, _ = e.check(Mode.BYPASS, _bash("rm build"), False)
    assert d == Decision.DENY


def test_allow_rule_skips_mode(tmp_path: Path) -> None:
    e = _engine_in(tmp_path, local=RuleSet(allow=[Rule("Bash", "git *", allow=True)]))
    d, _ = e.check(Mode.DEFAULT, _bash("git status"), False)
    assert d == Decision.ALLOW


def test_path_rule_matches_absolute_target(tmp_path: Path) -> None:
    """相对路径书写的文件规则（如 Write(src/**)）应匹配绝对路径调用（目标形态无关）。

    回归：匹配前把文件目标规整为项目相对路径，使命中与模型传入绝对/相对路径无关。
    """
    e = _engine_in(tmp_path, local=RuleSet(allow=[Rule("Write", "src/**", allow=True)]))
    # 绝对路径调用
    d, _ = e.check(Mode.DEFAULT, _write(str(tmp_path / "src" / "a" / "b.py")), False)
    assert d == Decision.ALLOW
    # 相对路径调用同样命中
    d, _ = e.check(Mode.DEFAULT, _write("src/a/b.py"), False)
    assert d == Decision.ALLOW
    # 不在 src 子树 -> 不命中 -> 落回 Ask
    d, _ = e.check(Mode.DEFAULT, _write("docs/x"), False)
    assert d == Decision.ASK


# ── 模式矩阵 ──


@pytest.mark.parametrize(
    "mode,cat,expected",
    [
        (Mode.DEFAULT, "READ", Decision.ALLOW),
        (Mode.DEFAULT, "WRITE", Decision.ASK),
        (Mode.DEFAULT, "EXEC", Decision.ASK),
        (Mode.ACCEPT_EDITS, "WRITE", Decision.ALLOW),
        (Mode.ACCEPT_EDITS, "EXEC", Decision.ASK),
        (Mode.PLAN, "WRITE", Decision.ASK),
        (Mode.PLAN, "EXEC", Decision.ASK),
        (Mode.PLAN, "READ", Decision.ALLOW),
        (Mode.BYPASS, "WRITE", Decision.ALLOW),
        (Mode.BYPASS, "EXEC", Decision.ALLOW),
        (Mode.BYPASS, "READ", Decision.ALLOW),
    ],
)
def test_mode_fallback_matrix(mode: Mode, cat: str, expected: Decision) -> None:
    from koyocode.permission import Category

    assert mode_fallback(mode, Category[cat]) == expected


# ── 三级优先级 ──


def test_three_level_priority_local_deny_beats_project_allow(tmp_path: Path) -> None:
    e = _engine_in(
        tmp_path,
        user=RuleSet(allow=[Rule("Bash", "git *", allow=True)]),
        project=RuleSet(allow=[Rule("Bash", "git *", allow=True)]),
        local=RuleSet(deny=[Rule("Bash", "git *", allow=False)]),
    )
    d, _ = e.check(Mode.BYPASS, _bash("git status"), False)
    assert d == Decision.DENY  # local 最近，deny 优先


def test_three_level_priority_project_beats_user(tmp_path: Path) -> None:
    e = _engine_in(
        tmp_path,
        user=RuleSet(deny=[Rule("Bash", "git *", allow=False)]),
        project=RuleSet(allow=[Rule("Bash", "git *", allow=True)]),
    )
    d, _ = e.check(Mode.BYPASS, _bash("git status"), False)
    assert d == Decision.ALLOW  # project 比 user 近


def test_three_level_nearest_hit_returns(tmp_path: Path) -> None:
    e = _engine_in(
        tmp_path,
        user=RuleSet(allow=[Rule("Bash", "*", allow=True)]),
        local=RuleSet(deny=[Rule("Bash", "rm *", allow=False)]),
    )
    # 匹配 rm 的命令：local deny 在 local 近命中
    d, _ = e.check(Mode.BYPASS, _bash("rm x"), False)
    assert d == Decision.DENY
    # 非 rm 的命令：local allow 无命中 -> project 无 -> user allow 命中
    d, _ = e.check(Mode.DEFAULT, _bash("git status"), False)
    assert d == Decision.ALLOW


# ── 降级 ──


def test_resolve_root_failure_returns_engine_and_err(tmp_path: Path) -> None:
    bad = str(tmp_path / "does-not-exist")
    e, err = new_engine(bad)
    assert e is not None
    assert err is not None
    assert e.start_mode() == Mode.DEFAULT
    # 仍能 check（沙箱用未解析 root；读 cwd 外被判越界属正常）
    d, _ = e.check(Mode.DEFAULT, _read("ok.py"), True)
    assert d in (Decision.ALLOW, Decision.DENY)


def test_bad_config_degrades_silently(tmp_path: Path) -> None:
    # 项目层配置坏：降级为空，不抛
    kdir = tmp_path / ".koyocode"
    kdir.mkdir()
    (kdir / "settings.yaml").write_text("permissions: [unterminated")
    e, err = new_engine(str(tmp_path))
    assert err is None  # 配置错不致致命 err
    # 规则为空：bash 走模式兜底 default -> Ask
    d, _ = e.check(Mode.DEFAULT, _bash("ls"), False)
    assert d == Decision.ASK


def test_persist_path_set(tmp_path: Path) -> None:
    e, _ = new_engine(str(tmp_path))
    assert e.local_path == str(tmp_path / ".koyocode" / "settings.local.yaml")
