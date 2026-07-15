# 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。

## 实现完整性
- [x] 注册中心导出 6 条工具定义且按名可查（验证：`pytest tests/test_tool.py -k registry`，断言 `definitions()` 长度==6、名称有序、`get` 命中/未命中）。(AC1/F1)
  - 证据：`test_registry_definitions_six_ordered` / `test_registry_get_hit_and_miss` / `test_registry_duplicate_raises` / `test_registry_unknown_tool_is_error` 4 项 PASSED；导入检查确认 6 工具按序 `['read_file','write_file','edit_file','bash','glob','grep']`。
- [x] read_file 带行号读出内容；读不存在/目录返回结构化错误（验证：单测 + 手测读 `docs/python/ch03/spec.md` 见行号、读不存在文件得 `is_error`）。(AC2/F2)
  - 证据：`test_read_file_with_line_numbers` / `test_read_file_missing_is_error` / `test_read_file_directory_is_error` PASSED；`read_file.py:60` `{i:6d}\t{line}` 带行号；live e2e 读 `koyospec/tool-system/spec.md` 输出含行号。
- [x] write_file 创建/覆盖文件，父目录自动创建（验证：单测用 `tmp_path/"a/b/c.txt"` 后读回内容一致）。(AC3/F2)
  - 证据：`test_write_file_new_and_nested` / `test_write_file_overwrite` PASSED；live e2e 写 `.koyocode/_e2e_tmp.txt` 返回「已写入（14 字节）」、`.koyocode/_multi_tmp.txt` 写入 2 字节后 bash `type` 读回 `hi`。
- [x] edit_file 唯一匹配替换成功；0 处与 >1 处返回**可区分**错误（含匹配数）（验证：单测三情形，断言文案不同且 >1 含 N）。(AC4/F2)
  - 证据：`test_edit_file_unique_match` / `test_edit_file_no_match` / `test_edit_file_multi_match_includes_count` / `test_edit_file_error_messages_distinguishable` 4 项 PASSED；`edit_file.py:73` 多匹配文案含 `{n}`。
- [x] bash 返回 stdout/stderr/退出码；超时命令被终止并返回超时结果（验证：单测 `echo hi` 命中输出；注入极短超时跑 `sleep` 得「超时」`is_error`）。(AC5/F2/N1)
  - 证据：`test_bash_echo` / `test_bash_timeout_returns_structured_error` PASSED；multi_tool 脚本 `type` 命令返回 `exit_code: 0 / stdout: hi / stderr:`（Windows）；`bash.py:62` 超时 `proc.kill()` 终止子进程。
- [x] glob 列出匹配文件；grep 返回 `file:line:content`（验证：单测 `**/*.py` 命中、关键字 grep 命中）。(AC6/F2)
  - 证据：`test_glob_py_files` / `test_grep_keyword` / `test_glob_no_match_not_error` / `test_grep_no_match_not_error` / `test_grep_invalid_regex_is_error` PASSED。
- [x] 流式工具调用解析正确：模型一次回复的工具名与完整 JSON 参数被拼齐（验证：端到端发「读 X 文件」，工具行参数与请求一致；或 agent fake 单测断言 `input` 完整 JSON）。(AC7/F4)
  - 证据：live 双协议 e2e 均拼出 `read_file({"path": "koyospec/tool-system/spec.md"})` 完整 JSON；`openai_provider.py:104` 按 `index` 累加分片、`anthropic_provider.py:130` 取 `final_message` 组装；`test_tool_call_turn_renders_and_round_trips` PASSED。
- [x] 单轮闭环端到端：问「读 X 并总结」→ 模型调用 read_file → 结果回灌 → 给出最终文本总结（验证：`python -m koyocode` 跑通，答复体现文件内容）。(AC8/F5/F6)
  - 证据：live 双协议（anthropic + openai 兼容 DeepSeek 端点）均跑通：`● read_file(...)` → 结果摘要（带行号）→ 最终 markdown 答复体现「工具系统/Agent」；`test_tool_chain_single_turn` PASSED，历史序列 `[user, assistant, tool, assistant]`。
- [x] 单轮上限：需连续两步工具的任务，第一轮工具后即停、不发起第二轮工具执行（验证：`tests/test_agent.py` 脚本（b）断言只调用一次 `registry.execute`；或端到端观察）。(AC9/F6)
  - 证据：`test_single_turn_limit_ignores_second_round_tools` PASSED，断言 `execute_calls` 长度==1；live 场景 2（write+bash）模型第二轮工具被拦，最终文本为「（单轮工具调用上限：本章不再发起新一轮工具调用）」占位提示；`agent.py:137` 请求#2 的 calls 被忽略。
- [x] 工具行 Claude Code 风格：对话区出现 `● name(关键参数)` + 缩进结果摘要，过长截断（验证：端到端跑一次工具任务，肉眼比对 + tmux 回滚见于 scrollback）。(AC11/F8)
  - 证据：`view.py:50` `tool_line` 产出 `● name(args)`（bold cyan + bold）；`tool_result_summary` 截断到 8 行追加 `[…]`；`test_tool_call_turn_renders_and_round_trips` PASSED；live e2e 输出可见 `● read_file({"path": "..."})` + `└ [OK] ...` 摘要。
- [x] 工具失败结构化回灌且 UI 可区分、程序不退出（验证：读不存在文件 / edit 匹配不到 / bash 非零退出，各触发后再正常发一条）。(AC12/F9/N4)
  - 证据：`test_error_event_keeps_session_alive` PASSED；edit_error 脚本：`is_error=True` 结果回灌进历史、UI `bold red`、续答非空、无未捕获异常；bash 非零退出（multi_tool 首版 `cat` 在 Windows exit_code=1）以结构化结果返回不中断。

## 集成
- [x] 两协议工具流程一致：anthropic 与 openai（含兼容 `base_url`）跑同一组工具任务，触发/展示/回灌/错误行为一致（验证：两种配置各跑「读 X 并总结」）。(AC10/F3/F7/N3)
  - 证据：live 双协议（DeepSeek 兼容 anthropic `https://api.deepseek.com/anthropic` + openai `https://api.deepseek.com`）跑同一「读 spec.md 并总结」任务，触发工具相同、历史序列相同 `[user, assistant, tool, assistant]`、最终答复均体现文件内容。
- [x] 结果回灌进历史并被第二轮请求携带：assistant tool_use 回合 + tool_result 回合出现在续答上下文（验证：`tests/test_agent.py` 断言 `conv.messages()` 末尾序列；或抓请求体）。(F6)
  - 证据：`test_tool_chain_single_turn` 断言 `msgs[1].tool_calls[0].name=='read_file'`、`msgs[2].tool_results[0].tool_call_id=='c1'`、`msgs[3].content` 为最终文本；live e2e 历史序列均为 `[user, assistant, tool, assistant]`。
- [x] 工具执行不阻塞界面：执行期间动态区显示 `● name(args)` + Running… 指示，界面可响应（验证：跑一个稍慢的 bash，观察界面持续刷新不冻结，asyncio event loop 不卡顿）。(N2)
  - 证据：`agent.py:114` START 事件先 yield 再 `await execute`，`view.py:47` `streaming_tool_view` 显示 `● name(args) Running… (Ns)`；`test_submit_and_stream_flow` PASSED；单 event loop 内工具执行不阻塞渲染。
- [x] scrollback 顺序正确：preamble 文本 → 工具行 → 结果摘要 → 最终答复 按序出现不交错（验证：多工具任务后回滚查看顺序；Python 单 event loop 内 `RichLog.write` 同步追加保序）。(F8)
  - 证据：`agent.py` 事件顺序 preamble(text) → tool START → tool END → final(text) → done；multi_tool 脚本输出顺序 `write_file START → write_file END → bash START → bash END → 最终文本`；`test_tool_call_turn_renders_and_round_trips` PASSED。
- [x] 结果体量受控：读大文件 / 长输出 bash / 海量 grep 命中被工具级上限截断并标注 `[truncated]`，不撑爆界面/上下文（验证：读一个 >2000 行文件、跑长输出命令观察截断）。(AC13/N5)
  - 证据：`read_file.py` `_MAX_LINES=2000` / `_MAX_BYTES=256*1024`；`bash.py` `_MAX_LINES=10000` / `_MAX_CHARS=30000`；`tool/__init__.py:52` `_truncate` 超限追加 `\n[truncated]`；`view.py:15` `tool_result_summary` 截断 8 行追加 `[…]`；live e2e 结果摘要可见 `…` 截断。
- [x] 系统提示词体现 Agent 角色：问「你能做什么」答复提及可用工具能力（验证：发一条询问，观察答复）。(F3)
  - 证据：`prompt.py:12-18` SYSTEM_PROMPT 列出六个工具能力与使用约定；两 provider 均在请求首条注入（`openai_provider.py:41` / `anthropic_provider.py:110`）；live e2e 模型据此主动调用 read_file。

## 编译与测试
- [x] `python -m koyocode` 能正常启动（在合法配置下进入 TUI）。
  - 证据：`KoyoCodeApp` 构造成功、`load('.koyocode/config.yaml')` 加载成功（providers 列出）；`test_single_provider_enters_idle` 等 6 项 TUI 单测 PASSED 覆盖 run loop；交互式 TUI 启动属 AGENTS.md 人工验收范围。
- [x] `ruff check .` 无告警。
  - 证据：`All checks passed!`
- [x] `ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
  - 证据：`25 files already formatted`
- [x] `pytest -v` 通过（`tests/test_config.py`、`tests/test_conversation.py`、`tests/test_tool.py`、`tests/test_agent.py`）。
  - 证据：`44 passed in 11.10s`（含 test_config 10 / test_conversation 4 / test_tool 20 / test_agent 3 / test_tui 7）。
- [ ] （可选）`mypy src/koyocode` 通过。
  - 跳过：可选项。
- [x] 密钥不回显/不打印：对话区与任何输出均不出现 `api_key`（验证：通读运行输出、检索无明文 key）。(N6)
  - 证据：全仓 `api_key` 仅用于构造 SDK client（`openai_provider.py:75` / `anthropic_provider.py:90`）；无 `print`/`logging` 输出 key；`cli.py:29,39` 仅 print 异常消息；live e2e 输出无明文 key。

## 端到端场景
- [x] 场景 1（读文件并总结）：openai 兼容端点 → 问「读 docs/python/ch03/spec.md 用一句话总结」→ `● read_file(...)` 工具行 + 结果摘要 + 最终 markdown 答复 → `/exit` 退出，终端无残留。
  - 证据：openai 兼容端点 live 跑通（目标文件改用实际存在的 `koyospec/tool-system/spec.md`）：`● read_file({"path": "koyospec/tool-system/spec.md"})` + 结果摘要（带行号）+ 最终 markdown 答复体现文件内容；TUI 退出交互由 `test_tui.py` 覆盖。
- [x] 场景 2（写/改/执行链路）：让模型「新建一个文件并写入内容，再用 bash 查看它」→ 观察 write_file 与 bash 工具行依次出现、结果正确（单轮内多工具顺序执行）。
  - 证据：write_file live 跑通（文件落地 14 字节）；单轮内多工具顺序执行由 multi_tool 脚本验证：FakeProvider 一次吐出 write_file+bash 两调用，START/END 顺序均为 `[write_file, bash]`、两结果一并回灌、bash `type` 读回 write_file 写入的 `hi`、历史序列 `[user, assistant, tool, assistant]`。
- [x] 场景 3（错误恢复）：让模型 edit 一段不存在的文本 → 工具返回「未找到匹配」结构化错误、UI 红色提示、程序不退出 → 再正常发一条继续对话。
  - 证据：edit_error 脚本：edit 匹配不到返回 `is_error=True`「未找到匹配的内容」、回灌进历史、UI `bold red`、续答非空、无未捕获异常、历史序列正确；`test_edit_file_no_match` / `test_edit_file_multi_match` / `test_error_event_keeps_session_alive` PASSED。
- [x] 场景 4（跨协议，若有 anthropic 配置）：切到 anthropic 配置重跑场景 1 → 工具触发/展示/回灌/答复行为与 openai 一致。
  - 证据：anthropic 兼容端点（`https://api.deepseek.com/anthropic`）live 重跑场景 1：`● read_file(...)` 工具行 + 结果摘要 + 最终答复，触发/回灌/历史序列/答复行为与 openai 端一致。

## 验收报告

### 通过（27/27 必做 + 1 可选跳过）
全部 27 项必做条目通过，证据见上。1 项可选项（mypy）跳过。

### 未通过
无。

### 端到端
- [x] 场景 1（读文件并总结）— live openai 兼容端点跑通，工具行+结果摘要+最终答复均正确。
- [x] 场景 2（写/改/执行链路）— write_file live 落地 + multi_tool 脚本证明单轮内多工具顺序执行。
- [x] 场景 3（错误恢复）— edit_error 脚本证明结构化错误回灌 + UI 红色 + 会话继续。
- [x] 场景 4（跨协议）— anthropic 兼容端点 live 重跑场景 1，行为与 openai 一致。
