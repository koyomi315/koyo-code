"""Agent ReAct 循环单测（AC1/AC3/AC4/AC6/AC8/AC9/AC13）。

用实现 ``Provider`` Protocol 的 ``FakeProvider`` 按脚本序列逐轮吐出 ``StreamEvent``：
- 场景 A：多轮链路（读文件 -> 续答），断言事件序列与历史。
- 场景 B：迭代上限--模型恒返工具调用，断言恰好 ``MAX_ITERATIONS`` 轮后停。
- 场景 C：连续未知工具--断言 ``MAX_UNKNOWN_RUN`` 轮后停；混入已知工具则计数重置。
- 场景 D：保序分批并发--插桩工具断言两只读并发、有副作用工具在其后开始、结果顺序保序。
- 场景 E：取消历史一致--执行期间取消，断言历史配对合法且可继续对话。
- 场景 F：Plan Mode 工具集与按轮次 reminder--断言只读工具、reminder 详略与不入历史。
ch05 新增：跨模式 stable 一致、缓存用量透传、普通模式全量工具。
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from koyocode.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    Event,
    Mode,
    Phase,
)
from koyocode.conversation import Conversation
from koyocode.llm import StreamEvent, ToolCall
from koyocode.llm import Usage as LLMUsage
from koyocode.permission import Outcome, new_engine
from koyocode.tool import Registry, Result, new_default_registry

_READ_TARGET = Path(__file__).resolve().parent.parent / "pyproject.toml"
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
_FULL_TOOLS = {"read_file", "write_file", "edit_file", "bash", "glob", "grep"}


def _engine(root: str = _REPO_ROOT):
    """构造根于仓库根的权限引擎（read_file 目标在子树内、避开沙箱）。"""
    engine, _ = new_engine(root)
    return engine


class FakeProvider:
    """按预设脚本序列依次吐出 ``StreamEvent``，实现 ``Provider`` Protocol。

    脚本耗尽后重放最后一个脚本（供「恒返工具调用」类用例复用同一脚本）。记录每次
    调用收到的 ``Request`` 供断言（``system.stable``/``environment``/``tools``/``reminder``）。
    """

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._i = 0
        self.calls = 0
        self.received_reqs: list = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):  # noqa: ANN001 - 实现 Protocol
        self.calls += 1
        self.received_reqs.append(req)
        script = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        for ev in script:
            await asyncio.sleep(0)
            yield ev


def _read_call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="read_file", input=json.dumps({"path": str(_READ_TARGET)}))


async def _collect(
    agent: Agent, conv: Conversation, mode: Mode, cancel: asyncio.Event
) -> list[Event]:
    events: list[Event] = []
    async for ev in agent.run(conv, mode, cancel):
        events.append(ev)
    return events


async def _collect_with_approval(
    agent: Agent,
    conv: Conversation,
    mode: Mode,
    outcomes: list,
    cancel: asyncio.Event | None = None,
) -> list[Event]:
    """``_collect`` 变体：遇 ``Event.approval`` 即按顺序回填 ``outcomes`` 中的决策。"""
    events: list[Event] = []
    cancel = cancel or asyncio.Event()
    idx = 0
    async for ev in agent.run(conv, mode, cancel):
        events.append(ev)
        if ev.approval is not None:
            outcome = outcomes[idx] if idx < len(outcomes) else outcomes[-1]
            idx += 1
            ev.approval.respond.set_result(outcome)
    return events


# ───────── 场景 A：多轮自动连环（AC1）─────────


@pytest.mark.asyncio
async def test_multi_turn_chain_ac1() -> None:
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(text="我先读取该文件"),
                StreamEvent(tool_calls=[_read_call("c1")]),
                StreamEvent(usage=LLMUsage(input_tokens=10, output_tokens=5)),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="已读取并给出总结"),
                StreamEvent(usage=LLMUsage(input_tokens=8, output_tokens=4)),
                StreamEvent(done=True),
            ],
        ]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("读 pyproject.toml 并总结")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    assert [e.iter for e in events if e.iter] == [1, 2]

    tool_events = [e for e in events if e.tool is not None]
    assert len(tool_events) == 2
    assert tool_events[0].tool.name == "read_file"
    assert tool_events[0].tool.phase == Phase.START
    assert tool_events[1].tool.phase == Phase.END
    assert tool_events[1].tool.is_error is False

    usages = [e.usage for e in events if e.usage is not None]
    assert [(u.input, u.output) for u in usages] == [(10, 5), (8, 4)]

    assert any(e.text == "我先读取该文件" for e in events)
    assert any(e.text == "已读取并给出总结" for e in events)
    assert events[-1].done is True

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].content == "我先读取该文件"
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].role == "tool"
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[3].content == "已读取并给出总结"


@pytest.mark.asyncio
async def test_no_tool_calls_direct_done() -> None:
    """自然完成（AC2）：无工具调用的纯文本直接结束循环。"""
    provider = FakeProvider(
        scripts=[[StreamEvent(text="你好"), StreamEvent(text="，世界"), StreamEvent(done=True)]]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("打招呼")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    assert [e.text for e in events if e.text] == ["你好", "，世界"]
    assert events[-1].done is True
    assert provider.calls == 1
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "你好，世界"


# ───────── 场景 B：迭代上限兜底（AC3）─────────


class EchoTool:
    """最小只读桩工具：立即返回固定结果。"""

    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "echo"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        return Result(content="ok")


@pytest.mark.asyncio
async def test_iteration_cap_stops_at_max_ac3() -> None:
    call = ToolCall(id="c1", name="echo", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    reg = Registry()
    reg.register(EchoTool())
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("keep going")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    assert provider.calls == MAX_ITERATIONS
    assert any(e.notice == NOTICE_MAX_ITER for e in events)
    assert events[-1].done is True
    assert conv.last_role() == "assistant"


# ───────── 场景 C：连续未知工具停止（AC4）─────────


@pytest.mark.asyncio
async def test_consecutive_unknown_tools_stops_ac4() -> None:
    call = ToolCall(id="c", name="ghost", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    reg = Registry()  # 空注册中心：ghost 永远未知
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    assert provider.calls == MAX_UNKNOWN_RUN
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert events[-1].done is True
    assert conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_unknown_run_resets_when_known_tool_appears() -> None:
    def ghost(cid: str) -> ToolCall:
        return ToolCall(id=cid, name="ghost", input="{}")

    def echo(cid: str) -> ToolCall:
        return ToolCall(id=cid, name="echo", input="{}")

    scripts = [
        [StreamEvent(tool_calls=[ghost("c1")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[ghost("c2")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[echo("c3")]), StreamEvent(done=True)],  # 已知工具，计数重置
        [StreamEvent(tool_calls=[ghost("c4")]), StreamEvent(done=True)],
        [StreamEvent(tool_calls=[ghost("c5")]), StreamEvent(done=True)],
        [StreamEvent(text="完成"), StreamEvent(done=True)],
    ]
    provider = FakeProvider(scripts=scripts)
    reg = Registry()
    reg.register(EchoTool())
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    assert provider.calls == 6
    assert not any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert events[-1].done is True


# ───────── 场景 D：保序分批并发（AC8/N6）─────────


class _ConcurrencyTracker:
    """记录只读/有副作用桩工具的开始结束时刻与并发峰值。"""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self.events: list[tuple[str, str, float]] = []

    def start(self, label: str) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)
        self.events.append((label, "start", time.monotonic()))

    def end(self, label: str) -> None:
        self.current -= 1
        self.events.append((label, "end", time.monotonic()))


class _ROStubTool:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self._tracker = tracker

    def name(self) -> str:
        return "stub_ro"

    def description(self) -> str:
        return "stub read-only"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["label"]
        self._tracker.start(label)
        await asyncio.sleep(0.05)
        self._tracker.end(label)
        return Result(content=f"{label}-done")


class _RWStubTool:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self._tracker = tracker

    def name(self) -> str:
        return "stub_rw"

    def description(self) -> str:
        return "stub side-effect"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["label"]
        self._tracker.start(label)
        await asyncio.sleep(0.01)
        self._tracker.end(label)
        return Result(content=f"{label}-done")


@pytest.mark.asyncio
async def test_ordered_batched_concurrency_ac8() -> None:
    tracker = _ConcurrencyTracker()
    reg = Registry()
    reg.register(_ROStubTool(tracker))
    reg.register(_RWStubTool(tracker))

    ro1 = ToolCall(id="ro1", name="stub_ro", input=json.dumps({"label": "ro1"}))
    ro2 = ToolCall(id="ro2", name="stub_ro", input=json.dumps({"label": "ro2"}))
    rw1 = ToolCall(id="rw1", name="stub_rw", input=json.dumps({"label": "rw1"}))

    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[ro1, ro2, rw1]), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("go")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    # 两只读并发峰值 >= 2
    assert tracker.peak >= 2

    ro_ends = [
        t for label, phase, t in tracker.events if phase == "end" and label in ("ro1", "ro2")
    ]
    rw_starts = [t for label, phase, t in tracker.events if phase == "start" and label == "rw1"]
    assert rw_starts[0] > max(ro_ends)

    # 结果按原始调用序回灌
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert [r.tool_call_id for r in tool_msg.tool_results] == ["ro1", "ro2", "rw1"]
    assert events[-1].done is True


# ───────── 场景 E：取消历史一致（AC9）─────────


class _SleepyTool:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    def name(self) -> str:
        return "sleepy"

    def description(self) -> str:
        return "stub"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(self._delay)
        return Result(content="finished")


@pytest.mark.asyncio
async def test_cancellation_keeps_history_consistent_ac9() -> None:
    reg = Registry()
    reg.register(_SleepyTool(delay=0.2))
    call = ToolCall(id="c1", name="sleepy", input="{}")
    provider = FakeProvider(scripts=[[StreamEvent(tool_calls=[call]), StreamEvent(done=True)]])
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("go slow")
    cancel = asyncio.Event()

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_soon())
    await _collect(agent, conv, Mode.BYPASS, cancel)
    await cancel_task

    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[2].tool_results[0].tool_call_id == "c1"
    assert msgs[3].content == NOTICE_CANCELLED
    assert conv.last_role() == "assistant"

    # 取消后历史未坏，可继续对话
    provider2 = FakeProvider(scripts=[[StreamEvent(text="继续没问题"), StreamEvent(done=True)]])
    agent2 = Agent(provider2, reg, "test", _engine())
    conv.add_user("继续")
    events2 = await _collect(agent2, conv, Mode.BYPASS, asyncio.Event())

    assert events2[-1].done is True
    assert conv.messages()[-1].content == "继续没问题"


# ───────── 场景 F：Plan Mode 工具集与按轮次 reminder（AC9/AC13）─────────


@pytest.mark.asyncio
async def test_plan_mode_uses_read_only_tools_and_reminder_ac13() -> None:
    provider = FakeProvider(scripts=[[StreamEvent(text="这是计划"), StreamEvent(done=True)]])
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("给登录功能加单测的方案")

    events = await _collect(agent, conv, Mode.PLAN, asyncio.Event())

    req = provider.received_reqs[0]
    # 规划模式首轮 reminder 为完整版（含标签、计划模式、read_file 约束、/do 等待）
    assert req.reminder.startswith("<system-reminder>")
    assert "计划模式" in req.reminder
    assert "read_file" in req.reminder
    assert "/do" in req.reminder
    # 规划模式仅放开只读工具
    assert [t.name for t in req.tools] == ["read_file", "glob", "grep"]
    # 系统提示稳定块与环境段均非空
    assert req.system.stable
    assert req.system.environment
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_plan_reminder_interval_full_then_concise_ac9() -> None:
    """规划模式按轮次注入：iter1 完整、iter5 完整（间隔 4）、iter2/3/4 精简（F7/AC9）。"""

    def ro(cid: str) -> ToolCall:
        return ToolCall(id=cid, name="read_file", input=json.dumps({"path": str(_READ_TARGET)}))

    # 前 4 轮各返一次只读调用，第 5 轮给出最终计划文本
    scripts = [[StreamEvent(tool_calls=[ro(f"c{i}")]), StreamEvent(done=True)] for i in range(4)]
    scripts.append([StreamEvent(text="done"), StreamEvent(done=True)])
    provider = FakeProvider(scripts=scripts)
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("调研并出计划")

    await _collect(agent, conv, Mode.PLAN, asyncio.Event())

    reqs = provider.received_reqs
    assert len(reqs) == 5
    # iter1 / iter5 为完整版（含 read_file 调研约束 + /do 等待）
    for idx in (0, 4):
        assert "read_file" in reqs[idx].reminder
        assert "/do" in reqs[idx].reminder
    assert reqs[4].reminder == reqs[0].reminder
    # iter2/3/4 为精简版（更短，仍含计划模式）
    for idx in (1, 2, 3):
        assert "计划模式" in reqs[idx].reminder
        assert len(reqs[idx].reminder) < len(reqs[0].reminder)


@pytest.mark.asyncio
async def test_normal_mode_no_reminder_and_full_tools() -> None:
    """普通模式无 reminder、tools 为全量（F7/AC9）。"""
    provider = FakeProvider(scripts=[[StreamEvent(text="ok"), StreamEvent(done=True)]])
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("hi")

    await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    req = provider.received_reqs[0]
    assert req.reminder == ""
    assert _FULL_TOOLS <= {t.name for t in req.tools}


@pytest.mark.asyncio
async def test_stable_prompt_same_across_modes_ac5() -> None:
    """普通与规划模式 req.system.stable 相同（规划提醒移出系统通道，F7/N1）。"""
    plan_provider = FakeProvider(scripts=[[StreamEvent(text="p"), StreamEvent(done=True)]])
    norm_provider = FakeProvider(scripts=[[StreamEvent(text="n"), StreamEvent(done=True)]])
    reg = new_default_registry()

    conv1 = Conversation()
    conv1.add_user("plan")
    await _collect(Agent(plan_provider, reg, "test", _engine()), conv1, Mode.PLAN, asyncio.Event())

    conv2 = Conversation()
    conv2.add_user("norm")
    await _collect(
        Agent(norm_provider, reg, "test", _engine()), conv2, Mode.BYPASS, asyncio.Event()
    )

    stable_plan = plan_provider.received_reqs[0].system.stable
    stable_norm = norm_provider.received_reqs[0].system.stable
    assert stable_plan
    assert stable_plan == stable_norm
    # 环境段均非空（含 working dir/platform/date/version/model）
    assert plan_provider.received_reqs[0].system.environment
    assert norm_provider.received_reqs[0].system.environment


@pytest.mark.asyncio
async def test_reminder_not_persisted_in_history_ac8() -> None:
    """reminder 不写入 conv 持久历史（F6/N3）。"""
    call = ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(_READ_TARGET)}))
    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("plan it")

    await _collect(agent, conv, Mode.PLAN, asyncio.Event())

    # 规划模式注入了 reminder，但 conv 持久历史不含 <system-reminder> 文本
    assert provider.received_reqs[0].reminder
    for m in conv.messages():
        assert "<system-reminder>" not in m.content


@pytest.mark.asyncio
async def test_cache_usage_passthrough_ac6() -> None:
    """fake 发 Usage(cache_write/cache_read) -> Event.usage 携带（F4/AC6）。"""
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(
                    usage=LLMUsage(input_tokens=100, output_tokens=20, cache_write=50, cache_read=0)
                ),
                StreamEvent(done=True),
            ]
        ]
    )
    reg = new_default_registry()
    agent = Agent(provider, reg, "test", _engine())

    conv = Conversation()
    conv.add_user("hi")

    events = await _collect(agent, conv, Mode.BYPASS, asyncio.Event())

    usages = [e.usage for e in events if e.usage is not None]
    assert usages
    assert usages[0].cache_write == 50
    assert usages[0].cache_read == 0
    assert usages[0].input == 100
    assert usages[0].output == 20


# ───────── 场景 G：权限系统集成（Deny/Ask/保序/并发/永久/取消，T9）─────────


def _write_call(path: str, cid: str = "w") -> ToolCall:
    return ToolCall(id=cid, name="write_file", input=json.dumps({"path": path, "content": "x"}))


def _read_call_at(path: str, cid: str = "r") -> ToolCall:
    return ToolCall(id=cid, name="read_file", input=json.dumps({"path": path}))


@pytest.mark.asyncio
async def test_deny_does_not_break_loop(tmp_path: Path) -> None:
    """Deny 回灌不中断：沙箱外路径被拒，Loop 继续到次轮给出文本。"""
    outside = str(tmp_path.parent / "koyo_outside_target.py")
    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[_read_call_at(outside, "c1")]), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("读外部文件")
    events = await _collect(agent, conv, Mode.DEFAULT, asyncio.Event())

    tool_end = [e for e in events if e.tool and e.tool.phase == Phase.END]
    assert tool_end and tool_end[0].tool.is_error is True  # 沙箱拒绝
    assert conv.last_role() == "assistant"
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_ordered_reflow_with_deny(tmp_path: Path) -> None:
    """保序回灌：单批含被拒 + 放行调用，结果按原下标序、id 配对正确。"""
    inner = str(tmp_path / "ok.txt")
    (tmp_path / "ok.txt").write_text("ok")
    outside = str(tmp_path.parent / "koyo_outside2.py")
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(tool_calls=[_read_call_at(outside, "c1"), _read_call_at(inner, "c2")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="ok"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("go")
    await _collect(agent, conv, Mode.DEFAULT, asyncio.Event())

    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert [r.tool_call_id for r in tool_msg.tool_results] == ["c1", "c2"]  # 保序
    assert [r.is_error for r in tool_msg.tool_results] == [True, False]  # 越界拒、内侧放行


@pytest.mark.asyncio
async def test_ask_approval_allow_once(tmp_path: Path) -> None:
    """Ask 人在回路：default 下 write_file 收 ApprovalRequest，回 ALLOW_ONCE 后执行生效。"""
    target = str(tmp_path / "out.txt")
    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[_write_call(target, "c1")]), StreamEvent(done=True)],
            [StreamEvent(text="写好了"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("写文件")
    events = await _collect_with_approval(agent, conv, Mode.DEFAULT, [Outcome.ALLOW_ONCE])

    approvals = [e for e in events if e.approval is not None]
    assert approvals and approvals[0].approval.name == "write_file"
    assert Path(target).exists()
    tool_end = [e for e in events if e.tool and e.tool.phase == Phase.END]
    assert tool_end[0].tool.is_error is False
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_ask_approval_deny_once(tmp_path: Path) -> None:
    """DENY_ONCE：拒绝后回灌被拒结果、不写文件、Loop 继续。"""
    target = str(tmp_path / "out2.txt")
    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[_write_call(target, "c1")]), StreamEvent(done=True)],
            [StreamEvent(text="了解，跳过"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("写文件")
    events = await _collect_with_approval(agent, conv, Mode.DEFAULT, [Outcome.DENY_ONCE])

    tool_end = [e for e in events if e.tool and e.tool.phase == Phase.END]
    assert tool_end[0].tool.is_error is True
    assert not Path(target).exists()
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_approval_allow_forever_writes_local(tmp_path: Path) -> None:
    """ALLOW_FOREVER：永久放行写本地层、当前执行生效。"""
    target = str(tmp_path / "out3.txt")
    provider = FakeProvider(
        scripts=[
            [StreamEvent(tool_calls=[_write_call(target, "c1")]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("写文件")
    events = await _collect_with_approval(agent, conv, Mode.DEFAULT, [Outcome.ALLOW_FOREVER])

    assert Path(target).exists()
    local_file = Path(engine.local_path)
    assert local_file.exists()
    assert "Write(" in local_file.read_text()
    _ = events


@pytest.mark.asyncio
async def test_read_only_batch_no_approval(tmp_path: Path) -> None:
    """只读批不产生任何 ApprovalRequest（N3 并发不退化）；越界只读得 errResult 其余仍完成。"""
    inner = str(tmp_path / "inner.txt")
    (tmp_path / "inner.txt").write_text("x")
    outside = str(tmp_path.parent / "koyo_outside3.py")
    provider = FakeProvider(
        scripts=[
            [
                StreamEvent(tool_calls=[_read_call_at(outside, "c1"), _read_call_at(inner, "c2")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="ok"), StreamEvent(done=True)],
        ]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("go")
    events = await _collect(agent, conv, Mode.DEFAULT, asyncio.Event())

    assert not any(e.approval is not None for e in events)  # 只读不触发审批
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    by_id = {r.tool_call_id: r for r in tool_msg.tool_results}
    assert by_id["c1"].is_error is True and by_id["c2"].is_error is False


@pytest.mark.asyncio
async def test_cancel_during_approval(tmp_path: Path) -> None:
    """ApprovalRequest 等待中取消：Loop 干净收尾、无挂起 task（N4）。"""
    target = str(tmp_path / "cancel.txt")
    provider = FakeProvider(
        scripts=[[StreamEvent(tool_calls=[_write_call(target, "c1")]), StreamEvent(done=True)]]
    )
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("写文件")
    cancel = asyncio.Event()

    async def _run() -> list[Event]:
        events: list[Event] = []
        async for ev in agent.run(conv, Mode.DEFAULT, cancel):
            events.append(ev)
            if ev.approval is not None:
                cancel.set()
                ev.approval.respond.set_result(Outcome.DENY_ONCE)
        return events

    await asyncio.wait_for(_run(), timeout=5)
    assert conv.last_role() == "assistant"
    assert not Path(target).exists()


@pytest.mark.asyncio
async def test_plan_mode_permission_uses_read_only_tools(tmp_path: Path) -> None:
    """plan 模式工具集仍只放开只读（沿用 ch05 断言，类型换 permission.Mode）。"""
    provider = FakeProvider(scripts=[[StreamEvent(text="计划"), StreamEvent(done=True)]])
    reg = new_default_registry()
    engine, _ = new_engine(str(tmp_path))
    agent = Agent(provider, reg, "test", engine)
    conv = Conversation()
    conv.add_user("给方案")
    events = await _collect(agent, conv, Mode.PLAN, asyncio.Event())

    req = provider.received_reqs[0]
    assert [t.name for t in req.tools] == ["read_file", "glob", "grep"]
    assert "计划模式" in req.reminder
    assert events[-1].done is True
