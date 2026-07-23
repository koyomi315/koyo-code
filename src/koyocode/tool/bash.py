"""bash 工具：在工作目录下执行 shell 命令，受超时约束。

返回 stdout / stderr / 退出码；非零退出按结果回灌（不设 is_error，让模型判断）；
超时由 ``Registry`` 层 ``asyncio.wait_for`` 触发，本工具在取消/超时时 ``proc.kill()``
终止子进程避免孤儿进程（AC5/N1）。
"""

import asyncio
import json
from typing import Any

from koyocode.tool import Result, _truncate

_MAX_LINES = 10000
_MAX_CHARS = 30000


class BashTool:
    """执行 shell 命令。"""

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在工作目录下执行 shell 命令；返回 stdout、stderr 与退出码。"
            "读文件、找文件、搜内容请优先用 read_file/glob/grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        }

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数 JSON 解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content=f"参数应为 JSON 对象，得到 {type(data).__name__}")

        command = data.get("command")
        if not isinstance(command, str) or not command:
            return Result(is_error=True, content="缺少参数 command（字符串）")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return Result(is_error=True, content=f"启动命令失败: {e}")

        try:
            stdout_b, stderr_b = await proc.communicate()
        finally:
            # 超时或取消时 Registry 的 wait_for 会取消本协程；
            # 此时子进程可能仍在运行，主动终止避免孤儿进程。
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 — 清理阶段不再抛
                    pass

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        text = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        # 非零退出不设 is_error：按结果回灌让模型判断
        return Result(content=_truncate(text, _MAX_LINES, _MAX_CHARS))
