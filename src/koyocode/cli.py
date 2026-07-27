"""入口装配：加载配置、构造权限引擎、启动 TUI。

banner 由 TUI 在 ``on_mount`` 时写入历史区（一次性），故此处不打印。
配置错误打印可读信息并以非零码退出（N4）；运行期异常同样不吐堆栈。

ch06：以项目根（cwd）构造 ``permission.new_engine`` 注入 TUI；引擎降级（仅 resolve_root
失败）仅告警不中断（engine 必非 None，agent check 不抛）。

ch07：在进入 TUI 前加载 MCP 配置、并发连接所有 server、把远端工具注册进 registry；
退出时统一关闭 Manager（``finally`` 兜底，5s 超时防卡死，F9/F11）。
"""

import asyncio
import sys
from pathlib import Path

from koyocode import __version__, permission
from koyocode import mcp as mcp_client
from koyocode.config import ConfigError, load
from koyocode.tool import new_default_registry
from koyocode.tui import new_app

CONFIG_PATH = ".koyocode/config.yaml"


def main() -> None:
    # Windows 控制台默认编码可能非 UTF-8，强制错误输出用 UTF-8，避免中文乱码。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    try:
        exit_code = asyncio.run(_amain())
    except KeyboardInterrupt:
        return
    except Exception as e:  # noqa: BLE001 - 启动期异常给可读提示而非堆栈
        print(f"koyoCode: 运行错误: {e}", file=sys.stderr)
        sys.exit(1)
    if exit_code:
        sys.exit(exit_code)


async def _amain() -> int:
    try:
        cfg = load(CONFIG_PATH)
    except ConfigError as e:
        print(f"koyoCode: {e}", file=sys.stderr)
        return 1

    root = str(Path.cwd().resolve())
    engine, err = permission.new_engine(root)
    if err is not None:
        print(f"koyoCode: 权限引擎降级: {err}", file=sys.stderr)

    registry = new_default_registry()
    # MCP：加载配置 -> 并发连接 server -> 注册远端工具；退出时统一关闭（F9/F11）。
    mcp_cfg = mcp_client.load_config(root)
    mgr = await mcp_client.new_manager(mcp_cfg, __version__)
    try:
        for t in mgr.tools():
            registry.register(t)
        app = new_app(cfg.providers, __version__, registry, engine)
        await app.run_async()
    finally:
        await mgr.close()
    return 0


if __name__ == "__main__":
    main()
