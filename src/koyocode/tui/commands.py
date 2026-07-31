"""TUI 斜杠命令分发（ch08）。

把现有 ``/exit`` / ``/plan`` / ``/do`` 迁移到统一注册表 ``BUILTIN_COMMANDS``，
新增 ``/compact``。未注册命令走友好提示。命令路径不写入 conversation、不调 LLM；
系统消息只在 TUI 视图层（历史区）展示。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from koyocode.agent.event import CompactEvent, CompactPhase
from koyocode.permission import Mode
from koyocode.prompt import EXECUTE_DIRECTIVE

if TYPE_CHECKING:
    from koyocode.tui.app import KoyoCodeApp

# 命令处理器签名：入参为 KoyoCodeApp
CommandHandler = Callable[["KoyoCodeApp"], Awaitable[None]]


def format_compact_notice(ev: CompactEvent) -> str:
    """按 phase 统一格式化压缩状态文案（自动 / 紧急 / 手动三路径共用）。"""
    if ev.phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if ev.phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    # AFTER_AUTO / AFTER_EMERGENCY
    if ev.err is not None:
        return f"压缩失败：{ev.err}"
    return f"已压缩，token 从 {ev.before} 降至 {ev.after}"


async def handle_exit(app: KoyoCodeApp) -> None:
    app._quit()


async def handle_plan(app: KoyoCodeApp) -> None:
    app.query_one("#input").clear()  # type: ignore[attr-defined]
    app.mode = Mode.PLAN
    app._append_history_text("● 已进入计划模式（仅只读工具，/do 切回执行）", "notice-message")
    app._update_statusbar()


async def handle_do(app: KoyoCodeApp) -> None:
    app.query_one("#input").clear()  # type: ignore[attr-defined]
    app._append_history_text("● /do", "user-message")
    app.mode = Mode.DEFAULT
    app.conv.add_user(EXECUTE_DIRECTIVE)
    app._update_statusbar()
    app._start_turn()


async def handle_compact(app: KoyoCodeApp) -> None:
    """手动 /compact：调 agent.run_force_compact，结果显示为系统消息。"""
    app.query_one("#input").clear()  # type: ignore[attr-defined]
    if app.agent is None:
        app._append_history_text("● 压缩失败：agent 未就绪", "error-message")
        return
    defs = (
        app._tool_registry.read_only_definitions()
        if app.mode == Mode.PLAN
        else app._tool_registry.definitions()
    )
    app._append_history_text("● 正在压缩上下文...", "notice-message")
    try:
        before, after = await app.agent.run_force_compact(app.conv, defs)
    except Exception as e:  # noqa: BLE001
        app._append_history_text(f"● 压缩失败: {e}", "error-message")
        return
    app._append_history_text(f"● 已压缩，token 从 {before} 降至 {after}", "notice-message")


async def _unknown_command(app: KoyoCodeApp, raw: str) -> None:
    app._append_history_text(
        f"● 未知命令: {raw}，可用命令: /exit /plan /do /compact", "notice-message"
    )


BUILTIN_COMMANDS: dict[str, CommandHandler] = {
    "/exit": handle_exit,
    "/plan": handle_plan,
    "/do": handle_do,
    "/compact": handle_compact,
}


def dispatch_command(text: str) -> tuple[CommandHandler | None, bool]:
    """检查输入是否以 ``/`` 开头。

    返回 ``(handler, is_command)``：
      - 不以 ``/`` 开头：``(None, False)``。
      - 命中注册命令：``(handler, True)``。
      - ``/`` 开头但未注册：返回一个 unknown handler，``(handler, True)``。
    """
    if not text.startswith("/"):
        return None, False
    stripped = text.strip()
    handler = BUILTIN_COMMANDS.get(stripped)
    if handler is not None:
        return handler, True

    async def _unknown(app: KoyoCodeApp) -> None:
        await _unknown_command(app, stripped)

    return _unknown, True
