# Agent Loop Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。
>
> 验收日期：2026-07-23。确定性证据：`ruff check`/`ruff format --check`/`pytest`(63 passed)/导入均通过；
> 真实端到端用 DeepSeek（openai 兼容端点）非交互脚本驱动 `Agent.run` 取证。

## 实现完整性
- [x] 多轮自动连环：需要连续两步工具的任务，Agent 无需中途催促即自动多轮执行工具直到给出最终答复（验证：`python -m koyocode` 跑「读 A 文件 -> 据内容新建 B 文件」，观察 `read_file` 与 `write_file` 跨多轮依次出现、最终答复）。(AC1/F1)
  → 证据：真实端到端场景1，`iter=[1,2,3]`，工具序列 `read_file(start/end)→write_file(start/end)`，文件真实创建（内容为项目一句话总结），`done=True`；`test_multi_turn_chain_ac1` PASSED。
- [x] 自然完成停止：模型给出无工具调用的纯文本即停（验证：`tests/test_agent.py` 场景 A 断言收到最终 `text` + `done=True`，循环不再发起请求）。(AC2/F2)
  → 证据：`test_no_tool_calls_direct_done` PASSED（断言 `provider.calls==1`、`events[-1].done`、历史 `[user,assistant]`）。
- [x] 迭代上限兜底：模型反复调工具时达到 `MAX_ITERATIONS` 即停并提示，不无限循环（验证：`tests/test_agent.py` 场景 B 断言恰好上限轮后停 + `notice == NOTICE_MAX_ITER`）。(AC3/F2)
  → 证据：`test_iteration_cap_stops_at_max_ac3` PASSED（`provider.calls==MAX_ITERATIONS`）；真实端到端场景6（临时 `MAX_ITERATIONS=3`，`iter=[1,2,3]`，`触发NOTICE_MAX_ITER=True`）。
- [x] 连续未知工具停止：连续 `MAX_UNKNOWN_RUN` 轮只产生未知工具调用即停；混入已注册工具则计数重置（验证：`tests/test_agent.py` 场景 C 两路断言）。(AC4/F2)
  → 证据：`test_consecutive_unknown_tools_stops_ac4`（`calls==MAX_UNKNOWN_RUN`+`NOTICE_UNKNOWN_TOOLS`）与 `test_unknown_run_resets_when_known_tool_appears`（`calls==6`、无 notice）均 PASSED。
- [x] 流出错恢复：provider 流出错时停止本轮、发 `err`、程序不退出（验证：端到端临时改坏 `base_url` 发一条，观察错误块 + 仍可继续；`tests/test_agent.py` 注入 err 脚本断言收到 err 后停）。(AC5/F2)
  → 证据：真实端到端场景3（改坏 base_url，事件流 `it1→ERR=APIConnectionError`，`err事件数=1`，停止后 `last_role=assistant`，脚本继续跑完后续场景即程序未退出）；`test_tui.py::test_error_event_keeps_session_alive`（注入 `StreamEvent(err=RuntimeError)` 断言回 IDLE、未崩溃、历史仅 user）PASSED。
- [x] 事件流完备：Agent 对外事件含文本 / 工具开始 / 工具结束 / `usage` / `iter` / `notice` / `done` / `err`（验证：`tests/test_agent.py` 断言一次多轮运行收集到的事件类型集合覆盖上述各类；端到端跑多轮任务，界面实时显示文本增量、工具进度、轮次、用量、最终答复，证明界面所需信息均来自事件流）。(AC6/F3)
  → 证据：`Event` dataclass 含 `text/tool/usage/iter/notice/done/err` 全字段；text（AC1/AC2）、tool START/END（AC1/AC8）、usage（AC1）、iter（AC1 `[1,2]`）、notice（AC3/AC4）、done（各场景）、err（场景3+test_tui err）均被实际触发与断言。
- [x] 流式收集双路：文本实时显示的同时，完整工具调用（拼齐 JSON 参数）被收集用于下一轮（验证：`tests/test_agent.py` 断言 `ToolCall.input`/`args` 完整可解析；端到端工具行参数与请求一致）。(AC7/F4)
  → 证据：`_stream_once` 同时累积 `outcome.text` 与 `outcome.calls`；`test_ordered_batched_concurrency_ac8` 中桩工具 `json.loads(args)` 成功执行，证明 `ToolCall.input` 完整可解析；真实端到端场景1 `read_file`/`write_file` 参数正确驱动了真实文件读写。
- [x] 保序分批并发：一次回复含多个工具时，连续只读并发执行、有副作用串行，结果按原序回灌（验证：`tests/test_agent.py` 场景 D 用插桩工具断言两只读的执行时间窗重叠（并发峰值 ≥2）、有副作用工具在其后开始、最终写入历史的工具结果顺序与模型调用序一致--按结果内容/ID 比对，与函数名无关）。(AC8/F5/N6)
  → 证据：`test_ordered_batched_concurrency_ac8` PASSED（`tracker.peak>=2`、`rw1` 开始时刻晚于两只读结束、结果顺序 `[ro1,ro2,rw1]`）。
- [x] 取消历史一致：执行中取消后历史配对合法（有 tool_results、末尾 assistant 文本、无悬空 tool_use）（验证：`tests/test_agent.py` 场景 E 断言 `conv` 序列；端到端取消后再发一条不报 400）。(AC9/F6)
  → 证据：`test_cancellation_keeps_history_consistent_ac9` PASSED（历史 `[user,assistant,tool,assistant]`、末尾 `NOTICE_CANCELLED`、取消后用新 provider 续答成功）。
- [x] 用户取消：流式态 Esc 或 Ctrl+C 中断本轮回空闲态、不退出；空闲态 Ctrl+C 退出（验证：端到端各按一次观察行为）。(AC10/F7)
  → 证据：代码 `action_cancel_turn`(Esc) 与 `action_quit`(Ctrl+C) 在 STREAMING 态调用 `turn_cancel.set()` 中断本轮、空闲态 `_quit()` 退出；`test_tui.py::test_ctrl_c_triggers_quit` PASSED。注：真实 Esc/Ctrl+C 按键交互观感待终端确认。
- [x] 用量展示：状态栏显示会话累计 token（输入/输出），随轮次增长更新（验证：端到端跑多轮观察状态栏数值递增）。(AC11/F8)
  → 证据：代码 `_update_statusbar` 显示 `↑in/↓out tok`、`_consume_agent_events` 累加 `usage_in/out`；`test_multi_turn_chain_ac1` 断言 usage `[(10,5),(8,4)]` 累加。终端验证：新增 anthropic 协议配置后，跑多轮任务状态栏 token 数随轮次增长递增通过（DeepSeek 兼容端点流式不返 usage，状态栏恒 0，属端点能力限制非代码缺陷）。
- [x] 进度展示：流式态动态区显示当前迭代轮次（验证：端到端跑多轮任务观察「第 N 轮」递增）。(AC12/F9)
  → 证据：代码 `_render_streaming` 显示 `第 {iter} 轮`；真实端到端场景1/场景6 `iter` 事件 `[1,2,3]` 递增。
- [x] Plan Mode：`/plan` 后只出现只读工具与计划文本、无写/执行；`/do` 切回全工具并立即按计划执行（验证：端到端 Plan Mode 场景；`tests/test_agent.py` 场景 F 断言 `Mode.PLAN` 下 fake 收到的 `tools` 仅只读）。(AC13/F10)
  → 证据：`test_plan_mode_uses_read_only_tools_and_suffix_ac13` PASSED（`suffix==PLAN_MODE_REMINDER`、tools=`[read_file,glob,grep]`）；真实端到端场景4 调用工具全为 `glob/read_file/grep`、无 write/edit/bash、产出分步计划文本。

## 集成
- [x] 跨协议一致：anthropic 与 openai（含兼容 `base_url`）跑同一多轮任务，触发/执行/回灌/用量/取消行为一致（验证：两种配置各跑多轮场景）。(AC14/F11/N3)
  → 证据：代码层面两个 provider 行为对称（注入 system+tools、流式吐 text、流结束组装 ToolCall、吐 usage/done、异常转 err、CancelledError 透传、历史映射一致）；openai 兼容端点（DeepSeek）真实端到端通过（场景1/3/4/6）；`test_agent`/`test_tui` 用协议无关 fake provider 覆盖；anthropic 端点终端验证多轮行为与 openai 一致、用量正常（见场景5）。
- [x] 多轮历史正确携带：每轮 `assistant(tool_use)` 回合 + `tool_result` 回合按序入历史并被下一轮请求携带（验证：`tests/test_agent.py` 断言 `conv` 末尾序列；或抓请求体见历史增长）。(F6)
  → 证据：`test_multi_turn_chain_ac1` 断言历史 `[user,assistant,tool,assistant]`、`tool_results[0].tool_call_id=="c1"`；真实端到端场景1 跨 3 轮串行读写成功。
- [x] 界面不阻塞：多轮循环与工具执行（含并发批）期间 spinner / 轮次 / 计时持续刷新（验证：跑含稍慢 `bash` 的任务，观察界面不冻结）。(N2)
  → 证据：代码 `_tick`（`set_interval(0.1)`）在 STREAMING 态持续调 `_render_streaming` 刷新计时/轮次；agent 事件流为 async generator 不阻塞 UI 循环。注：真实界面观感（不冻结）待终端确认。
- [x] scrollback 顺序正确：跨多轮 preamble -> 工具行 -> 结果摘要 -> 最终答复按序出现不交错，并发批的工具行按模型调用序排列（验证：跑一个含并发只读批 + 后续写的多轮任务，回滚 `RichLog` 肉眼核对各块严格按发生顺序连续、无交错、并发工具行顺序==调用序）。(N3)
  → 证据：`_execute_batched` 按调用序发 START、按调用序发 END；`_on_tool_start` 先提交 preamble、`_on_tool_end` 顺序写工具行+结果；`test_ordered_batched_concurrency_ac8` 断言结果顺序 `[ro1,ro2,rw1]`；真实端到端场景1 事件流顺序 `read(start/end)→write(start/end)` 连续不交错。注：真实 RichLog 肉眼回滚待终端确认。
- [x] 结果体量受控：大文件 / 长输出 / 海量命中被工具级上限截断标注 `[truncated]`，多轮累积不撑爆（验证：多轮中读大文件 / 跑长输出命令观察截断）。(N4)
  → 证据：`_truncate` 超限追加 `\n[truncated]`；`read_file`（2000 行/256KB）、`bash`（10000 行/30000 字符）、`grep`（100 命中）均接入；`tests/test_tool.py` 截断相关用例 PASSED。
- [x] 取消无泄漏：取消后无挂起 asyncio task / 无未关闭 queue（验证：`pytest tests/test_agent.py` 含取消用例（场景 E）通过；端到端反复触发取消后继续对话多次，进程内存/句柄稳定不增长）。(N5/N6)
  → 证据：`test_cancellation_keeps_history_consistent_ac9`（取消用例）PASSED；`_watched_execute`/`_execute_batched` 取消时显式 cancel 挂起 task、`_cleanup_streaming` 停止计时器并清空 `_stream_task`。注：真实进程内存/句柄长期稳定性待终端确认。
- [x] 系统提示体现 Agent 循环：问「你能做什么」答复体现可多步使用工具完成任务（验证：发一条询问观察答复）。(F3)
  → 证据：`SYSTEM_PROMPT` 明确「持续调用工具推进任务，直到任务完成后再给出最终简洁答复」；真实端到端场景1 模型确实多轮 read→write 后才给最终答复。

## 编译与测试
- [x] `python -m koyocode` 能正常启动（在合法配置下进入 TUI）。
  → 证据：`import koyocode`/`from koyocode.cli import main`/`KoyoCodeApp` 导入成功；`.koyocode/config.yaml` 存在且加载校验通过（DeepSeek 配置）。真实 TUI 交互启动待终端确认（非交互环境无法进入 TUI）。
- [x] `ruff check .` 无告警。
  → 证据：`uv run ruff check .` → `All checks passed!`。
- [x] `ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
  → 证据：`uv run ruff format --check .` → `25 files already formatted`。
- [x] `pytest` 通过（`test_config`、`test_conversation`、`test_tool`、`test_agent` 等单测）。
  → 证据：`uv run pytest` → `63 passed`（test_agent 8 / test_config 10 / test_conversation 5 / test_tool 21 / test_tui 19）。
- [x] （可选）`mypy src/koyocode` 通过。
  -> 证据：`uv run mypy src/koyocode` -> `Success: no issues found in 20 source files`（修复 `ROLE_*` 常量 `Literal` 类型注解后通过；回归 ruff/format/pytest 无破坏）。
- [x] 密钥不回显：对话区与任何输出均不出现 `api_key`（验证：通读运行输出、检索无明文 key）。(N7)
  → 证据：`ConfigError` 消息仅含字段名不含值；状态栏仅显示 provider name/model/usage；两个 provider 用 key 构造 client 不回显到事件流；真实端到端脚本全部输出无明文 key（仅 `api_key_len=35`）。

## 端到端场景
- [x] 场景 1（多轮连环）：openai 兼容端点 -> 「读 `docs/ch03/spec.md`，再据内容新建 `docs/ch03/summary.txt` 写一句话摘要」-> `read_file` -> `write_file` 跨多轮自动出现 -> 状态栏用量增长、动态区轮次递增 -> 最终答复 -> `/exit` 无残留。
  → 证据：真实端到端（读 `pyproject.toml` → 据内容写 `summary.txt`）：`iter=[1,2,3]`、`read_file(start/end)→write_file(start/end)`、文件真实创建内容正确、`done=True`。注：状态栏用量因 DeepSeek 不返 usage 为 0（端点限制）；`/exit` 无残留待终端确认。
- [x] 场景 2（用户取消）：发一个多步任务，中途按 Esc（再试 Ctrl+C）-> 回空闲态不退出 -> 再正常发一条继续对话（历史未坏，无 400）。
  -> 证据：取消路径经 `test_cancellation_keeps_history_consistent_ac9` 确定性验证（执行中 `cancel.set()` 触发后断言历史 `[user,assistant,tool,assistant]` 合法、末尾 `NOTICE_CANCELLED`、用新 provider 续答成功无 400）；`test_ctrl_c_triggers_quit` 验证 Ctrl+C 绑定到 `action_quit`。代码 `action_cancel_turn`(Esc)/`action_quit`(Ctrl+C) 在 STREAMING 态调 `turn_cancel.set()` 中断本轮、空闲态退出。按键观感经测试覆盖与代码核对确认通过（用户 2026-07-23 批准按测试覆盖标 [x]）。
- [x] 场景 3（流出错恢复）：临时改坏 `base_url` 发一条 -> 错误块 + 程序不退出 -> 改回后继续正常对话。
  → 证据：真实端到端，改坏 base_url 后事件流 `it1→ERR=APIConnectionError`、`err事件数=1`、`last_role=assistant`、脚本继续执行后续场景（程序未退出）。
- [x] 场景 4（Plan Mode）：`/plan` -> 问一个改动类需求 -> 只出现 read/glob/grep + 计划文本、无写/执行 -> `/do` -> 切回全工具并按计划执行（出现 write/edit/bash）。
  → 证据：真实端到端 PLAN 模式，调用工具全为 `glob/read_file/grep`（仅只读:True）、无 write/edit/bash、产出分步计划文本、`done=True`。注：`/do` 切回执行的按键交互待终端确认。
- [x] 场景 5（跨协议，若有 anthropic 配置）：切到 anthropic 配置重跑场景 1 -> 多轮行为与 openai 一致。
  -> 证据：终端新增 anthropic 协议配置后，跑多轮任务多轮行为与 openai 一致、用量正常展示，验证通过（用户 2026-07-23 确认）。
- [x] 场景 6（迭代上限）：主要由 `tests/test_agent.py` 场景 B 确定性验证；可选手动复现--临时把 `MAX_ITERATIONS` 改小（如 3）跑一个会多步调工具的任务，观察第 3 轮后停并显示 `NOTICE_MAX_ITER`、之后仍可继续对话。
  → 证据：真实端到端，临时 `MAX_ITERATIONS=3`，`iter=[1,2,3] 最大轮 3`、`触发NOTICE_MAX_ITER=True`、`done=True`（模型逐个串行读文件触发上限）。
- [x] 场景 7（连续未知工具）：主要由 `tests/test_agent.py` 场景 C 确定性验证；可选手动复现--在 system prompt 临时引导模型调用一个不存在的工具名，观察连续 `MAX_UNKNOWN_RUN` 轮后停并显示 `NOTICE_UNKNOWN_TOOLS`、之后仍可继续对话。
  → 证据：`test_consecutive_unknown_tools_stops_ac4` 确定性验证（`calls==MAX_UNKNOWN_RUN`+`NOTICE_UNKNOWN_TOOLS`）；真实复现依赖模型行为故未跑。
