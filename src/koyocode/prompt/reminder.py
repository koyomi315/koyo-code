"""补充消息注入与规划模式提醒（F6/F7/AC8/AC9）。

``system_reminder`` 用 ``<system-reminder>`` 标签包裹补充指令，让模型理解为系统
补充上下文而非用户提问（不针对其直接回复）。此类消息每轮动态构造、不写入持久
历史，故不污染缓存、不破坏历史角色交替（N3）。

规划模式提醒由本机制承载（不再拼到系统提示尾部，F7）：``plan_reminder(full)``
按 Agent Loop 轮次返回完整版或精简版。``EXECUTE_DIRECTIVE`` 为 ``/do`` 注入的
用户消息文案（从 ``prompt`` 包顶层重导出，import 路径不变）。
"""

_PLAN_REMINDER_FULL = """\
当前处于计划模式：你只能使用只读工具（read_file、glob、grep）调研代码库，
不得写文件、改文件或执行命令。请据调研结果产出一份清晰的分步执行计划，
写完计划后停下，等待用户用 /do 批准后再动手执行。"""

_PLAN_REMINDER_CONCISE = "计划模式：仅用只读工具调研，产出分步计划后停下等 /do 批准。"

EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"


def system_reminder(body: str) -> str:
    """用 ``<system-reminder>`` 标签包裹 ``body``（F6/AC8）。"""
    return f"<system-reminder>\n{body}\n</system-reminder>"


def plan_reminder(full: bool) -> str:
    """返回包好标签的规划模式提醒：``full`` 取完整版，否则精简版（F7/AC9）。"""
    return system_reminder(_PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE)


__all__ = [
    "EXECUTE_DIRECTIVE",
    "plan_reminder",
    "system_reminder",
]
