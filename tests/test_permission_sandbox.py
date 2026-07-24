"""路径沙箱单测（N2）：含祖先回退、软链接逃逸、项目子树判定。"""

import os
import sys
from pathlib import Path

import pytest

from koyocode.permission.sandbox import (
    resolve_root,
    sandbox_ok,
)


class _StubEngine:
    """仅提供 root 字段的最小桩（sandbox_ok 鸭子类型依赖）。"""

    def __init__(self, root: str) -> None:
        self.root = root


def test_resolve_root_strict(tmp_path: Path) -> None:
    assert resolve_root(str(tmp_path)) == str(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_root(str(tmp_path / "does-not-exist"))


def test_sandbox_inner_file_passes(tmp_path: Path) -> None:
    engine = _StubEngine(str(tmp_path))
    inner = tmp_path / "a.txt"
    inner.write_text("x")
    assert sandbox_ok(engine, str(inner))


def test_sandbox_new_file_with_missing_dirs_uses_ancestor(tmp_path: Path) -> None:
    """root 内、含多级未创建中间目录的新建文件路径通过（专测祖先回退分支）。"""
    engine = _StubEngine(str(tmp_path))
    new_file = tmp_path / "deep" / "nested" / "missing" / "new.py"
    assert sandbox_ok(engine, str(new_file))


def test_sandbox_outside_rejected(tmp_path: Path) -> None:
    engine = _StubEngine(str(tmp_path))
    # /etc/passwd 在 root 外（假设 /etc 存在且不在 tmp 子树）
    assert not sandbox_ok(engine, "/etc/passwd")
    # 相对 .. 逃逸
    assert not sandbox_ok(engine, "../outside")


def test_sandbox_empty_path_is_root(tmp_path: Path) -> None:
    engine = _StubEngine(str(tmp_path))
    assert sandbox_ok(engine, "")


def test_sandbox_symlink_escape_rejected(tmp_path: Path) -> None:
    """root 内指向 root 外目录的软链接应被拒（resolve 跟随到真实目标越界）。"""
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = root / "escape.link"
    link.symlink_to(outside)
    engine = _StubEngine(str(resolve_root(str(root))))
    assert not sandbox_ok(engine, str(link))


def test_sandbox_symlink_inner_passes(tmp_path: Path) -> None:
    """root 内指向 root 内文件的软链接通过。"""
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("x")
    link = root / "inner.link"
    link.symlink_to(target)
    engine = _StubEngine(str(resolve_root(str(root))))
    assert sandbox_ok(engine, str(link))


@pytest.mark.skipif(sys.platform == "win32", reason="暂跳 POSIX 特性")
def test_sandbox_relative_resolved_against_root(tmp_path: Path) -> None:
    engine = _StubEngine(str(tmp_path))
    # 相对路径相对 root 解析
    assert sandbox_ok(engine, "sub/dir/file.txt")
    assert not sandbox_ok(engine, os.path.join("..", "..", "escape"))
