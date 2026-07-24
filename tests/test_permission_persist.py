"""永久放行写入单测：persist_local_allow 写文件、reload 仍 Allow、幂等。"""

import json
from pathlib import Path

import pytest

from koyocode.llm import ToolCall
from koyocode.permission import Decision, Mode
from koyocode.permission.engine import new_engine
from koyocode.permission.persist import escape_glob, rule_for


def _bash(cmd: str) -> ToolCall:
    return ToolCall(id="1", name="bash", input=json.dumps({"command": cmd}))


def _write(path: str) -> ToolCall:
    return ToolCall(id="1", name="write_file", input=json.dumps({"path": path, "content": "x"}))


def test_rule_for_bash_exact_no_glob_wildcard() -> None:
    rule, yaml_str, ok = rule_for(_bash("git push * origin"), root="/r")
    assert ok
    assert yaml_str == "Bash(git push \\* origin)"


def test_rule_for_write_relpath(tmp_path: Path) -> None:
    f = tmp_path / "sub" / "a.py"
    engine, _ = new_engine(str(tmp_path))
    rule, yaml_str, ok = rule_for(_write(str(f)), root=engine.root)
    assert ok
    assert yaml_str.startswith("Write(")
    assert "a.py" in yaml_str


def test_persist_local_allow_writes_file_and_reloads(tmp_path: Path) -> None:
    engine, _ = new_engine(str(tmp_path))
    engine.persist_local_allow(_bash("git status"))
    assert Path(engine.local_path).exists()
    content = Path(engine.local_path).read_text()
    assert "Bash(git status)" in content
    # reload 后规则仍在：bash 该命令从 Ask 变成 Allow
    e2, _ = new_engine(str(tmp_path))
    d, _ = e2.check(Mode.DEFAULT, _bash("git status"), False)
    assert d == Decision.ALLOW


def test_persist_local_allow_idempotent(tmp_path: Path) -> None:
    engine, _ = new_engine(str(tmp_path))
    engine.persist_local_allow(_bash("git status"))
    engine.persist_local_allow(_bash("git status"))  # 重复不抛不重复写
    content = Path(engine.local_path).read_text()
    assert content.count("Bash(git status)") == 1


def test_persist_then_sandbox_still_blocks_outside(tmp_path: Path) -> None:
    engine, _ = new_engine(str(tmp_path))
    engine.persist_local_allow(_write(str(tmp_path / "ok.txt")))
    # 写 root 外路径仍被沙箱拦（规则只对精确路径生效）
    d, _ = engine.check(Mode.BYPASS, _write("/etc/passwd"), False)
    assert d == Decision.DENY