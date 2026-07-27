"""永久放行写入单测：persist_local_allow 写文件、reload 仍 Allow、幂等。"""

import json
from pathlib import Path

from koyocode.llm import ToolUseBlock
from koyocode.permission import Decision, Mode
from koyocode.permission.engine import new_engine
from koyocode.permission.persist import rule_for


def _bash(cmd: str) -> ToolUseBlock:
    return ToolUseBlock(id="1", name="bash", input=json.dumps({"command": cmd}))


def _write(path: str) -> ToolUseBlock:
    return ToolUseBlock(id="1", name="write_file", input=json.dumps({"path": path, "content": "x"}))


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


def test_persist_write_abs_path_reloads_to_allow(tmp_path: Path) -> None:
    """绝对路径 write_file 选「永久」后，重载引擎对同调用仍判 Allow（目标形态无关）。

    回归：持久化规则按项目相对路径存储，匹配前同样规整目标，故绝对路径调用也能命中。
    """
    abs_target = str(tmp_path / "sub" / "a.py")
    engine, _ = new_engine(str(tmp_path))
    engine.persist_local_allow(_write(abs_target))
    # 重载后同绝对路径调用 -> Allow（不弹窗）
    e2, _ = new_engine(str(tmp_path))
    d, _ = e2.check(Mode.DEFAULT, _write(abs_target), False)
    assert d == Decision.ALLOW
