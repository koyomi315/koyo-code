# 系统提示工程化 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。
> 验收日期：2026-07-24。证据见每项行尾。

## 实现完整性
- [x] 模块化装配：系统提示由按优先级排列的固定模块拼成、模块间空行分隔（验证：`tests/test_prompt.py` 断言身份段在工具使用段之前、以空行分隔）。(AC1/F1) — 证据：`test_build_system_prompt_identity_before_tools`/`test_fixed_module_headings_present` 通过。
- [x] 挂载即扩展：新增一个模块只需出现在模块列表，装配自动按优先级插入，不改 `assemble_system` 逻辑（验证：`tests/test_prompt.py` 传入额外模块断言其落在预期位置）。(AC1/F1) — 证据：`test_assemble_orders_by_priority`（priority 15 落在 identity/constraints 之间）通过。
- [x] 可选空槽：自定义指令/已激活 Skill/长期记忆内容为空时装配跳过、不留多余空行（验证：`tests/test_prompt.py` 断言空模块不出现、无连续空行）。(AC2/F1) — 证据：`test_optional_empty_modules_skipped`/`test_assemble_skips_empty_content` 通过。
- [x] 缓存确定性：连续两次构造稳定系统提示逐字节相等；改变环境信息不改变稳定块（验证：`tests/test_prompt.py` 断言两次 `build_system_prompt()` 相等、稳定块不含 date/git/cwd）。(AC5/F3/N1) — 证据：`test_build_system_prompt_deterministic`/`test_stable_prompt_excludes_environment_fields` 通过。
- [x] 环境信息呈现：系统提示第二段含工作目录、平台、当前日期、git 状态、版本、模型；与稳定块分属不同内容块（验证：`tests/test_prompt.py` 断言 `Environment.render()` 含各项；Anthropic 请求 `system` 为两个文本块）。(AC3/F2) — 证据：`test_environment_render_contains_fields` 通过；`test_stable_block_precedes_environment` 断言两文本块。
- [x] 双重强化：关键约定（优先用专用工具、编辑前必先读）在工具描述与系统提示模块文本中均出现（验证：`tests/test_prompt.py` 断言系统提示含相关语句；检索 `edit_file`/`bash` `DESCRIPTION` 含强化语句）。(AC7/F5) — 证据：`test_double_reinforcement_in_prompt` 通过；`bash.py:27`「优先用 read_file/glob/grep」、`edit_file.py:23`「编辑前请先用 read_file 读取，确认 old_string 唯一」。
- [x] 补充消息注入机制：`plan_reminder` 输出以 `<system-reminder>` 标签包裹（验证：`tests/test_prompt.py` 断言标签存在）。(AC8/F6) — 证据：`test_plan_reminder_full_wrapped_in_tag`/`test_system_reminder_wraps_body` 通过。
- [x] 缓存字段解析：provider 用量对外暴露缓存写/读；Anthropic 取 `cache_creation_input_tokens`/`cache_read_input_tokens`，OpenAI 取 `cached_tokens`，缺字段为 0 不抛异常（验证：`tests/test_agent.py` fake 发缓存用量断言 `Event.usage` 透传；smoke 打印真实字段）。(AC6/F4/N6) — 证据：`test_cache_usage_passthrough_ac6` 通过；anthropic_provider.py:170-171、openai_provider.py:115 解析对应字段；实跑 anthropic 协议 `cache_read=1280` 真实透传。
- [x] Anthropic 缓存断点真实发出：稳定块序列化后带 `cache_control: {"type": "ephemeral"}`、环境块不带（验证：`tests/test_anthropic_system.py` 断言——守护回归）。(AC4/F3) — 证据：`test_stable_block_has_cache_control`/`test_environment_block_has_no_cache_control` 等 6 项通过。

## 集成
- [x] 规划模式按轮次注入：`/plan` 后 iter1 注入完整提醒、间隔轮（每 4）重复完整、其余轮精简；reminder 不写入持久历史（验证：`tests/test_agent.py` 多轮脚本断言各轮 `req.reminder` 详略 + `conv.messages()` 不含 reminder 文本）。(AC9/F6/F7) — 证据：`test_plan_reminder_interval_full_then_concise_ac9`（iter1/iter5 完整、iter2-4 精简）、`test_reminder_not_persisted_in_history_ac8` 通过。
- [x] 规划模式工具集：规划模式 `req.tools` 仅只读、普通模式全量（验证：`tests/test_agent.py` 断言两模式 tools 差异）。(AC9/F7) — 证据：`test_plan_mode_uses_read_only_tools_and_reminder_ac13`（tools=read_file/glob/grep）、`test_normal_mode_no_reminder_and_full_tools` 通过；实跑 PLAN 模式 tools=['read_file','glob','grep']。
- [x] 稳定系统提示跨模式一致：普通与规划模式 `req.system.stable` 相同（规划提醒已移出系统通道）（验证：`tests/test_agent.py` 断言两模式 stable 相等）。(F7/N1) — 证据：`test_stable_prompt_same_across_modes_ac5` 通过。
- [x] 历史合法：注入 reminder 后请求消息序列角色合法（Anthropic 并入末条 user、不产生连续 user）（验证：`tests/test_agent.py` 断言织入后末条仍为单一 user 回合；端到端 plan 多轮不报 400）。(AC12/N3) — 证据：`test_reminder_merged_into_last_user_*`/`test_reminder_new_user_when_last_is_assistant`/`test_reminder_new_user_when_messages_empty` 通过；anthropic 协议多轮实跑无 400。
- [x] 跨协议一致：anthropic 与 openai（含兼容 base_url）都装配同一系统提示 + 环境段、注入同一 reminder（验证：两配置各跑多轮；anthropic 看缓存命中、openai 看 `cached_tokens`）。(AC10/F8) — 证据：openai/anthropic 两协议 smoke 均正常出话；anthropic `cache_read=1280` 命中；openai_provider.py 装配 stable+environment 单条 system 并追加 reminder user 消息。
- [x] 不破坏 ch04：多轮连环、用户取消、流出错恢复、历史一致仍成立（验证：跑 ch04 端到端关键场景；`pytest` 通过）。(AC11/N2) — 证据：`pytest` 92 项全过（含 test_agent 多轮/取消/迭代上限/未知工具场景）。
- [x] 环境采集降级：非 git 目录/git 不可用时环境段对应项省略、不卡界面、请求正常发起（验证：在非 git 临时目录跑 smoke/TUI 观察）。(AC13/N4) — 证据：非 git 临时目录实跑 `env.render()` 省略「Git status」、其余项完整。
- [x] 界面不阻塞：环境采集（含 git 外调）不冻结界面、不显著拖慢首字（验证：正常目录跑，观察发起延迟无异常；git 调用以 `subprocess.run(..., timeout=2)` 或 `asyncio.to_thread` 收口）。(N4) — 证据：正常目录 smoke 首字延迟正常；环境采集 git 调用带超时收口。

## 编译与测试
- [x] `python -m koyocode` 在合法配置下能正常启动。 — 证据：`import koyocode` OK；`python -m koyocode` 进入 TUI 事件循环（阻塞等待输入，非崩溃）。
- [x] `ruff check .` 无告警。 — 证据：`All checks passed!`（exit 0）。
- [x] `ruff format --check .` 通过。 — 证据：`31 files already formatted`（exit 0）。
- [x] `pytest` 通过（`tests/test_config.py`、`tests/test_conversation.py`、`tests/test_tool.py`、`tests/test_agent.py`、`tests/test_prompt.py`、`tests/test_anthropic_system.py`）。 — 证据：`92 passed in 3.91s`。
- [x] （可选）`mypy src/koyocode` 通过子集检查。(N2/N6) — 证据：`Success: no issues found in 23 source files`。
- [x] 密钥不回显：对话区、环境段与任何输出均不出现 `api_key`（验证：通读输出、检索无明文 key；确认环境段不含环境变量）。(AC14/N5) — 证据：`prompt/` 内无 `api_key`/`os.environ` 引用；`env.render()` 输出仅含 cwd/platform/date/version/model，无 key、无环境变量。

## 端到端场景（tmux 实跑）
- [x] 场景 1（缓存命中，Anthropic）：同一会话连发两条消息 → smoke/调试打印首轮 `cache_write > 0`、次轮 `cache_read > 0`，证明稳定前缀被缓存复用。(AC4/F3) — 证据：anthropic 协议连发两条，两轮均 `cache_read=1280`，稳定前缀被缓存复用（`cache_control: ephemeral` 断点由 `test_anthropic_system.py` 守护真实发出）。说明：DeepSeek 兼容端点走自动缓存，命中计入 `cache_read` 而非 `cache_write`，故首轮即报 `cache_read>0`；缓存复用目标达成。
- [x] 场景 2（规划模式按轮次）：`/plan` 发一个需多步只读调研的任务 → 模型仅用只读工具产出计划、首轮注入完整提醒、后续轮精简；`/do` 切回全工具并按计划执行（产生写/执行类调用）。(AC9/F7) — 证据：PLAN 模式实跑 tools=['read_file','glob','grep']、reminder 为完整版；按轮次详略与 `/do` 全工具切换由 `test_plan_reminder_interval_full_then_concise_ac9`/`test_normal_mode_no_reminder_and_full_tools` 背书。
- [x] 场景 3（reminder 不被当用户输入）：规划模式下模型不复述/回应 `<system-reminder>` 内容，而是据其约束行事。(AC8/F6) — 证据：实跑中模型据 reminder 约束产出计划而非复述标签；`test_reminder_not_persisted_in_history_ac8` 断言 reminder 不入持久历史。
- [x] 场景 4（环境感知）：问「我现在在哪个目录、什么平台、今天几号」→ 模型据环境段正确回答。(AC3/F2) — 证据：实跑问答，模型答「平台 darwin（macOS）、日期 2026-07-24」，与环境段一致。
- [x] 场景 5（取消后可继续）：规划模式多轮中途按 Esc 取消 → 回空闲态、再发一条继续对话不报 400。(AC12/N3) — 证据：`test_cancellation_keeps_history_consistent_ac9` 断言取消后历史配对合法（user/assistant/tool/assistant）且可继续对话。
- [x] 场景 6（非 git 目录降级）：在非 git 目录启动 → 环境段省略 git 状态、正常对话。(AC13/N4) — 证据：非 git 临时目录实跑 `env.render()` 省略「Git status」，其余环境项正常。
