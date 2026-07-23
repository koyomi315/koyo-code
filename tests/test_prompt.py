"""prompt 包单测：模块化装配、跳空槽、缓存确定性、双重强化、环境与 reminder（T4）。

覆盖 AC1/AC2/AC3/AC5/AC7/AC8 与 F1/F2/F3/F5/F6。
"""

from koyocode.prompt import (
    EXECUTE_DIRECTIVE,
    Environment,
    Module,
    assemble_system,
    build_system_prompt,
    gather_environment,
    plan_reminder,
    system_reminder,
)
from koyocode.prompt.modules import fixed_modules, optional_modules


# ───────── 装配顺序与空行分隔（AC1/F1）─────────
def test_build_system_prompt_identity_before_tools() -> None:
    """身份段出现在工具使用段之前，模块间以空行分隔。"""
    sp = build_system_prompt()
    identity_idx = sp.find("KoyoCode")
    tools_idx = sp.find("## 工具使用")
    assert identity_idx != -1
    assert tools_idx != -1
    assert identity_idx < tools_idx
    # 模块间以空行分隔
    assert "\n\n" in sp


def test_fixed_module_headings_present() -> None:
    """七个固定模块标题均出现在装配结果中。"""
    sp = build_system_prompt()
    for heading in (
        "## 边界",
        "## 任务执行（ReAct）",
        "## 何时行动",
        "## 工具使用",
        "## 语气与风格",
        "## 文本输出",
    ):
        assert heading in sp


# ───────── 跳空槽（AC2/F1）─────────
def test_optional_empty_modules_skipped() -> None:
    """optional_modules 内容为空，装配后不留连续空行、不以空行起止。"""
    sp = build_system_prompt()
    assert not sp.startswith("\n")
    assert not sp.endswith("\n\n")
    assert "\n\n\n" not in sp


def test_assemble_skips_empty_content() -> None:
    """assemble_system 跳过空 content 模块，不产生连续空行。"""
    mods = [
        Module(name="a", priority=10, content="AAA"),
        Module(name="empty", priority=20, content=""),
        Module(name="b", priority=30, content="BBB"),
    ]
    result = assemble_system(mods)
    assert result == "AAA\n\nBBB"
    assert "\n\n\n" not in result


def test_assemble_orders_by_priority() -> None:
    """新增模块按优先级插入预期位置（挂载即扩展，AC1/F1/N8）。"""
    extra = Module(name="extra", priority=15, content="EXTRA MODULE")
    sp = assemble_system(fixed_modules() + [extra] + optional_modules())
    idx_identity = sp.find("KoyoCode")
    idx_extra = sp.find("EXTRA MODULE")
    idx_constraints = sp.find("## 边界")
    # priority 15 落在 identity(10) 与 constraints(20) 之间
    assert idx_identity < idx_extra < idx_constraints


# ───────── N1 缓存确定性（AC5/F3/N1）─────────
def test_build_system_prompt_deterministic() -> None:
    """连续两次构造稳定系统提示逐字节相等。"""
    assert build_system_prompt() == build_system_prompt()


def test_stable_prompt_excludes_environment_fields() -> None:
    """稳定块不含 date/git/cwd 等随轮次/时间变化的成分（N1）。"""
    sp = build_system_prompt()
    assert "## Environment" not in sp
    assert "Working directory" not in sp
    assert "Git status" not in sp


# ───────── F5 双重强化（AC7/F5）─────────
def test_double_reinforcement_in_prompt() -> None:
    """系统提示含「优先用专用工具」「编辑前必先读」的表述。"""
    sp = build_system_prompt()
    # 优先用专用工具而非 shell 拼凑
    assert "优先使用专用工具" in sp
    assert "read_file" in sp
    assert "glob" in sp
    assert "grep" in sp
    # 编辑文件前必先读
    assert "编辑文件前必须先用 read_file 读取" in sp
    assert "old_string 唯一" in sp


# ───────── 环境信息（AC3/F2）─────────
def test_environment_render_contains_fields() -> None:
    """Environment.render 含工作目录/平台/日期/版本/模型。"""
    env = gather_environment("test-ver", "test-model")
    r = env.render()
    assert "Working directory" in r
    assert "Platform" in r
    assert "Date" in r
    assert "Version: test-ver" in r
    assert "Model: test-model" in r


def test_environment_render_omits_empty_fields() -> None:
    """空值项省略（N4 降级）。"""
    env = Environment(
        working_dir="/tmp",
        platform="darwin",
        date="2026-01-01",
        git_status="",
        version="v",
        model="m",
    )
    r = env.render()
    assert "Git status" not in r
    assert "Working directory: /tmp" in r


# ───────── reminder（AC8/F6）─────────
def test_plan_reminder_full_wrapped_in_tag() -> None:
    """plan_reminder(True) 含 <system-reminder> 标签与完整文案。"""
    full = plan_reminder(True)
    assert full.startswith("<system-reminder>\n")
    assert full.endswith("\n</system-reminder>")
    assert "计划模式" in full
    assert "read_file" in full
    assert "/do" in full


def test_plan_reminder_concise_shorter_than_full() -> None:
    """plan_reminder(False) 用精简文案，比完整版短。"""
    full = plan_reminder(True)
    concise = plan_reminder(False)
    assert concise.startswith("<system-reminder>\n")
    assert concise.endswith("\n</system-reminder>")
    assert len(concise) < len(full)
    assert "计划模式" in concise


def test_system_reminder_wraps_body() -> None:
    """system_reminder 用标签包裹 body。"""
    assert system_reminder("hello") == "<system-reminder>\nhello\n</system-reminder>"


def test_execute_directive_nonempty() -> None:
    """EXECUTE_DIRECTIVE 为 /do 注入的非空文案。"""
    assert isinstance(EXECUTE_DIRECTIVE, str)
    assert EXECUTE_DIRECTIVE.strip()
