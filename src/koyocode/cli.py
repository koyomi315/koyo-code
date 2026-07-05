"""入口装配：加载配置、启动 TUI。

banner 由 TUI 在 ``on_mount`` 时写入 ``RichLog``（一次性），故此处不打印。
配置错误打印可读信息并以非零码退出（N4）；运行期异常同样不吐堆栈。
"""

import sys

from koyocode.config import ConfigError, load
from koyocode.tool import new_default_registry
from koyocode.tui import KoyoCodeApp

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
        cfg = load(CONFIG_PATH)
    except ConfigError as e:
        print(f"koyoCode: {e}", file=sys.stderr)
        sys.exit(1)

    registry = new_default_registry()
    app = KoyoCodeApp(cfg.providers, registry)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001 — 启动期异常给可读提示而非堆栈
        print(f"koyoCode: 运行错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
