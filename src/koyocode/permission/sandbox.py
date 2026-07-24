"""路径沙箱（N2）：把文件类工具的访问限制在项目根目录子树内。

关键点：

- 防软链接逃逸：对存在的目标用 ``Path.resolve(strict=True)``（跟随符号链接到真实绝对路径）。
- 新建文件（含未创建的中间目录）：对不存在的目标，逐级回退到最近**已存在祖先**目录
  ``resolve(strict=True)`` 后拼回剩余段——避免 ``Path.resolve(strict=True)`` 因目标不存在
  抛错而误判。
- 不硬编码路径分隔符：用 ``pathlib`` 与 ``os.sep``。
- bash 命令执行不做路径围栏（无法可靠静态解析任意命令的文件访问，交黑名单+规则+模式）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["eval_symlinks_or_ancestor", "relative_to_root", "resolve_root", "sandbox_ok"]


def relative_to_root(root: str, target: str) -> str:
    """规整文件目标为项目相对 slash 路径（规则匹配与持久化规则生成共用，保证两侧一致）。

    规则里的文件模式按项目相对路径书写（如 ``Write(src/**)``），而工具调用传入的 ``path``
    可能是绝对路径或含符号链接。本函数把任一形态统一为相对 ``root`` 的 slash 路径，使
    命中判定与目标传入形态无关：

    - 绝对路径：解析符号链接后取相对 ``root`` 的路径（``/var/.../root/src/a.py`` -> ``src/a.py``）。
    - 相对路径：视为相对项目根，原样保留（仅规范分隔符）。
    - ``root`` 外或解析失败：原样返回（沙箱已先拦，此处仅兜底）。
    """
    p = Path(target)
    root_resolved = Path(root).resolve(strict=False)
    try:
        if p.is_absolute():
            rel = p.resolve(strict=False).relative_to(root_resolved)
        else:
            rel = (root_resolved / p).relative_to(root_resolved)
        return str(rel).replace("\\", "/")
    except (ValueError, OSError):
        return target.replace("\\", "/")


def resolve_root(root: str) -> str:
    """解析项目根：展开用户与符号链接、``strict=True`` 要求存在；失败抛 ``FileNotFoundError``。"""
    return str(Path(root).expanduser().resolve(strict=True))


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """尽量解析符号链接的目标绝对路径。

    - 目标存在：``Path(abs_path).resolve(strict=True)``（跟随软链接到真实路径）。
    - 目标不存在（新建文件 / 含未创建中间目录）：逐级回退到最近**已存在祖先**目录，
      对该祖先 ``resolve(strict=True)`` 后把剩余相对段拼回（保留待创建部分的字面形态）。
      若整条路径都不存在（连根都取不到已存在祖先），回退到对整串非严格 resolve。
    """
    p = Path(abs_path)
    try:
        return str(p.resolve(strict=True))
    except (FileNotFoundError, RuntimeError, OSError):
        pass
    # 回退：找最近的已存在祖先
    ancestor = p
    rest_parts: list[str] = []
    while ancestor is not None and not ancestor.exists():
        rest_parts.append(ancestor.name)
        ancestor = ancestor.parent
    if ancestor is not None and ancestor.exists():
        base = ancestor.resolve(strict=True)
        if rest_parts:
            return str(base.joinpath(*reversed(rest_parts)))
        return str(base)
    # 退化：非严格解析（不跟随软链接、不要求存在）
    return str(p.resolve(strict=False))


def _is_within(root: str, resolved: str) -> bool:
    """``resolved`` 是否等于或在 ``root`` 子树内（用 ``os.sep`` 前缀避免前缀误匹配）。"""
    return resolved == root or resolved.startswith(root + os.sep)


@runtime_checkable
class _HasRoot(Protocol):
    """沙箱判定所需的最小依赖：仅需要 ``root`` 字段（真实 ``Engine`` 满足）。"""

    root: str


def sandbox_ok(engine: _HasRoot, path: str) -> bool:
    """``path`` 是否在 ``engine.root`` 子树内（N2）。

    - 空 ``path`` 视为 ``engine.root``（通过）。
    - 相对路径相对 ``engine.root`` 解析为绝对。
    - 经 ``eval_symlinks_or_ancestor`` 解析符号链接后再做前缀比对。
    """
    root = engine.root
    if not path:
        return True
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    resolved = eval_symlinks_or_ancestor(str(p))
    return _is_within(root, resolved)
