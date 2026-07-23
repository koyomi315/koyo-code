"""端到端 smoke：验证缓存策略真实生效（F3/F4/AC4）。

连发两条消息，打印每轮用量（input/output/cache_write/cache_read），
预期首轮 ``cache_write > 0``、次轮 ``cache_read > 0``（稳定前缀命中复用）。
需合法配置 ``.koyocode/config.yaml`` 与可用 API key。
"""

import asyncio
import sys

from koyocode.agent import Agent, Mode
from koyocode.config import ConfigError, load
from koyocode.conversation import Conversation
from koyocode.llm import new_provider
from koyocode.tool import new_default_registry

CONFIG_PATH = ".koyocode/config.yaml"


async def main() -> None:
    try:
        cfg = load(CONFIG_PATH)
    except ConfigError as e:
        print(f"koyoCode: 配置错误: {e}", file=sys.stderr)
        sys.exit(1)
    if not cfg.providers:
        print("koyoCode: 无可用 provider", file=sys.stderr)
        sys.exit(1)

    provider = new_provider(cfg.providers[0])
    registry = new_default_registry()
    agent = Agent(provider, registry, "smoke")
    cancel = asyncio.Event()

    # 同一会话连发两条消息：历史累积但稳定前缀（system+tools）不变，
    # 故次轮请求命中首轮写入的缓存。
    conv = Conversation()
    for turn, msg in enumerate(("你好，请用一句话介绍你能做什么。", "再简短一些。"), start=1):
        conv.add_user(msg)
        print(f"\n=== 第 {turn} 条消息 ===")
        async for ev in agent.run(conv, Mode.NORMAL, cancel):
            if ev.usage is not None:
                print(
                    "usage: "
                    f"input={ev.usage.input} output={ev.usage.output} "
                    f"cache_write={ev.usage.cache_write} cache_read={ev.usage.cache_read}"
                )
            if ev.err is not None:
                print(f"ERROR: {ev.err}", file=sys.stderr)
                return
            if ev.text:
                print(ev.text, end="", flush=True)
            if ev.done:
                print()


if __name__ == "__main__":
    asyncio.run(main())
