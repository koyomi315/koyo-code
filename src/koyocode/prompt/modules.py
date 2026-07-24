"""系统提示固定模块与可选空槽（F1/AC1/AC2）。

每个 ``Module`` 带名称、优先级、正文：``fixed_modules`` 返回七个固定模块，
``optional_modules`` 返回三个预留空槽（``content`` 为空时装配自动跳过，AC2）。
新增一类指令只需定义新模块挂到对应优先级，不改装配主逻辑（N8）。

稳定模块正文不含任何随轮次/时间变化的成分（N1 缓存确定性）；环境与时间
相关内容只进 ``environment``，绝不进稳定模块。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    """系统提示的一个职责模块。

    ``priority`` 数值越小排越前（固定模块 10..70，可选模块 80..100）；
    ``content`` 为空时装配自动跳过（可选空槽，AC2）。
    """

    name: str
    priority: int
    content: str


# 固定模块正文：中文，参考 ch04 SYSTEM_PROMPT 的内容要点重写为模块化结构。

_IDENTITY = """\
你是 KoyoCode，一个运行于终端的 AI 编程助手，通过调用工具完成编程任务。\
你耐心、简洁、务实。"""

_CONSTRAINTS = """\
## 边界
- 仅在工作目录内操作；未经请求不要访问目录之外的文件。
- 绝不在输出中泄露密钥、API key 或凭据。
- 对破坏性操作（删除、覆盖、强制命令）保持谨慎；行动前先确认意图。"""

_TASK_MODE = """\
## 任务执行（ReAct）
- 分步推进：思考、调用工具、观察结果，如此反复。
- 修改文件前先读取它；先理解上下文。
- 持续调用工具以推进任务；仅在完成时给出最终答案。"""

_ACTIONS = """\
## 何时行动
- 当需要获取信息或执行操作时，调用工具。
- 若干连续的只读调用可一并发出；有副作用的调用应当审慎。\
- 依据工具结果采取行动，而非逐字复述。"""

# F5 双重强化：优先用专用工具、编辑前必先读——在此模块与 tool DESCRIPTION 中双重表述。
_TOOLS = """\
## 工具使用
- 优先使用专用工具而非 shell：用 read_file/glob/grep 来读取文件、查找文件、\
搜索内容，而非拼接 bash 命令。
- 编辑文件前必须先用 read_file 读取，并确认 old_string 唯一后再替换。
- 用 read_file 读取、write_file 写入、edit_file 精确替换、bash 执行命令、\
glob 按模式查找、grep 搜索内容。
- 向工具传入清晰、合法的 JSON 参数。"""

_TONE = """\
## 语气与风格
- 简洁直接；不要奉承。
- 用与用户提问相同的语言作答。
- 不确定时如实说明；不要编造事实。"""

_OUTPUT = """\
## 文本输出
- 代码、步骤与对比使用 Markdown（带语言标签的代码块、列表、强调）。\
- 最终答案保持聚焦、简洁；不要过度解释。"""


def fixed_modules() -> list[Module]:
    """七个固定模块，按优先级 10..70 排（身份 -> 系统约束 -> 任务模式 -> 动作执行
    -> 工具使用 -> 语气风格 -> 文本输出）。"""
    return [
        Module(name="identity", priority=10, content=_IDENTITY),
        Module(name="constraints", priority=20, content=_CONSTRAINTS),
        Module(name="task-mode", priority=30, content=_TASK_MODE),
        Module(name="actions", priority=40, content=_ACTIONS),
        Module(name="tools", priority=50, content=_TOOLS),
        Module(name="tone", priority=60, content=_TONE),
        Module(name="output", priority=70, content=_OUTPUT),
    ]


def optional_modules() -> list[Module]:
    """三个预留空槽（自定义指令/已激活 Skill/长期记忆），内容为空时装配跳过（AC2）。

    本章不接入真实内容来源（留待后续章节）。
    """
    return [
        Module(name="custom-instructions", priority=80, content=""),
        Module(name="active-skills", priority=90, content=""),
        Module(name="long-term-memory", priority=100, content=""),
    ]


__all__ = ["Module", "fixed_modules", "optional_modules"]
