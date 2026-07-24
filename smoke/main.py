"""smoke：非交互冒烟，验证权限引擎 + Agent 链路（Mode.BYPASS 跳过人在回路）。

非交互无法人在回路，故以 ``Mode.BYPASS`` 运行（黑名单/沙箱仍拦，Ask 不触发阻塞）。
用内置 ``FakeProvider`` 按脚本吐出一次 ``write_file`` 调用 + 续答，确认：

- 引擎注入后 ``Agent`` 正常构造；
- ``BYPASS`` 下 ``write_file``（cwd 子树内目标）不被 Ask 阻塞、执行生效；
- 事件流与历史合法收尾。

运行：``python -m smoke`` 或 ``uv run python -m smoke``。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from koyocode.agent import Agent, Mode
from koyocode.conversation import Conversation
from koyocode.llm import StreamEvent, ToolCall
from koyocode.permission import new_engine
from koyocode.tool import new_default_registry


class FakeProvider:
    """脚本化 provider：第一轮请求 write_file、第二轮给文本答复。"""

    name = "smoke"
    model = "smoke-model"

    def __init__(self) -> None:
        self._i = 0

    async def stream(self, req):  # noqa: ANN001 - 实现 Protocol
        self._i += 1
        if self._i == 1:
            target = str(Path.cwd() / "smoke_out.txt")
            yield StreamEvent(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        input=json.dumps({"path": target, "content": "smoke ok\n"}),
                    )
                ]
            )
            yield StreamEvent(done=True)
        else:
            yield StreamEvent(text="smoke 写入完成")
            yield StreamEvent(done=True)


async def _run_smoke() -> None:
    cwd = str(Path.cwd().resolve())
    engine, err = new_engine(cwd)
    if err is not None:
        print(f"[smoke] 权限引擎降级: {err}")

    provider = FakeProvider()
    registry = new_default_registry()
    agent = Agent(provider, registry, "smoke", engine)

    conv = Conversation()
    conv.add_user("smoke: 写一个文件")

    cancel = asyncio.Event()
    async for ev in agent.run(conv, Mode.BYPASS, cancel):
        if ev.tool is not None:
            phase = "start" if ev.tool.phase.value == "start" else "end"
            print(f"[smoke] tool {ev.tool.name} {phase}: {ev.tool.result!r} err={ev.tool.is_error}")
        elif ev.text:
            print(f"[smoke] text: {ev.text}")
        elif ev.err is not None:
            print(f"[smoke] err: {ev.err}")
            break
        if ev.done:
            print("[smoke] done")

    target = Path.cwd() / "smoke_out.txt"
    assert target.exists(), "smoke 未写入文件"
    print(f"[smoke] 写入校验通过: {target} ({target.stat().st_size} bytes)")


def main() -> None:
    asyncio.run(_run_smoke())


if __name__ == "__main__":
    main()
