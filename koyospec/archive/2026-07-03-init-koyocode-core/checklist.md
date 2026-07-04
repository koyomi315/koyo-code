# 多协议 LLM 终端对话客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式。

## 实现完整性

> 标注：`[x]` = 已由单测/Pilot/真实调用验证；`[ ]` = 需在 T14 人工 e2e 肉眼确认（视觉/交互细节）。

- [x] 配置加载：合法 `.koyocode/config.yaml` 能解析出 providers 列表（`test_config.py` + `config.load` 验证）。(AC1/F1)
- [x] 配置校验：缺密钥/非法 protocol/文件缺失时给出可读错误并非零退出，无未捕获堆栈（`test_config.py` 全覆盖；删 config 运行实测退出码 1）。(AC1/N4)
- [x] 单 provider 直进：仅一条配置时启动直接进入对话（Pilot `test_single_provider_enters_idle` 验证状态机 IDLE）。(AC2/F2)
- [x] 多 provider 选择：多条配置时出现方向键 `OptionList`，选定后进入对话（Pilot `test_multi_provider_selection` 验证 SELECTING→选定→IDLE）。(AC2/F2)
- [x] 内置 system prompt 与历史随请求发送（anthropic 适配器经代理真实调用，回复体现内置 prompt；`test_conversation.py` 验证历史顺序）。(AC4/F4)
- [x] thinking：anthropic 配 `thinking: true` 时启用，且界面不出现任何思考文本（经代理真实调用 `thinking=True` 收到正文 + done；`text_stream` 自动丢弃思考增量）。(AC5/F5)
- [ ] 流式逐字：回复以纯文本逐字出现（适配器流式 + Pilot 事件流已验证；肉眼逐字观感见 T14）。(AC5/F8)
- [ ] markdown 定型：回复结束后整段以 markdown 渲染（`assistant_block` 用 `rich.markdown.Markdown`；代码块/列表渲染观感见 T14）。(AC8/F8)
- [x] 多行输入：Alt+Enter 换行、Enter 提交、提交后输入框清空（Pilot `test_alt_enter_inserts_newline` + `test_submit_and_stream_flow` 验证）。(AC9/F9)
- [ ] 响应计时：自提交即显示 `Imagining… (Ns)` 且秒数递增，结束后显示总耗时（`_tick`/`_render_streaming` 计时逻辑已实现；秒数递增观感见 T14）。(AC12/F12)
- [x] 错误反馈：错误 key/不存在模型时，错误在对话区可区分样式（红色）显示且不退出（Pilot `test_error_event_keeps_session_alive` + openai 真实 401 `AuthenticationError`→err 事件；`error_block` 红色样式）。(AC11/F11)
- [x] 退出：`/exit` 与 Ctrl+C 均能安全退出（Pilot `test_ctrl_c_triggers_quit` 验证 Ctrl+C 触发 `action_quit`；`/exit`→`_quit` 代码路径已实现）。终端恢复正常（raw mode 还原）由 Textual 自动处理，见 T14。(AC10/F10/N7)
- [ ] 界面布局：启动含猫 banner + 名称版本 + cwd + 就绪提示行 + 输入框（含 `❯` 与占位符）+ 状态栏（左 name 右 model）（`compose` 已含全部组件；截图比对见 T14）。(AC7/F7)

## 集成

- [ ] TUI 通过统一 `Provider` Protocol 驱动两种协议，切换协议不改变上层交互（验证：分别用 anthropic / openai 配置跑同一组对话，行为一致）。(AC3/N3)
- [ ] 多轮上下文携带：先告知信息、后追问，模型能正确引用前文；退出再启动后历史为空（验证：两轮对话 + 重启验证）。(AC6/F6)
- [ ] 流式不阻塞：等待/流式期间界面仍响应、不冻结（验证：长回复期间界面持续刷新；asyncio event loop 不阻塞）。(AC13/N1)
- [ ] scrollback 渲染（Claude Code 风格）：完成的消息（用户输入/助手回复/错误）追加到 `RichLog`，可用终端原生滚轮/Textual 滚动回看，退出后内容保留在终端历史中；动态区仅含输入框 + 正在流式的回复 + 状态栏（验证：tmux 多轮后回滚查看历史 + 退出后历史仍在）。
- [ ] base_url 覆盖：为某 provider 配自定义 `base_url`（兼容端点）可正常收发（验证：配一个兼容端点跑通一轮）。(F3)
- [ ] 窗口自适应：缩放终端宽度后输入框/对话区/markdown 不错版（验证：运行中调整终端宽度）。(N6)

## 编译与测试

- [x] `python -m koyocode` 能正常启动（无配置实测打印清晰错误 + 退出码 1；合法配置下进入 TUI 见 T14）。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过（`ruff format .` 已统一格式）。
- [x] `pytest` 通过（19 项：`test_config.py` 10 + `test_conversation.py` 3 + `test_tui.py` 6）。
- [x] （可选）`mypy src/koyocode` 通过（`Success: no issues found in 12 source files`）。
- [x] 密钥不回显/不打印：通读代码——`api_key` 仅传入 SDK 构造器，任何输出路径（banner/状态栏/错误/对话区）均不引用 `api_key`。config.yaml 已在 `.gitignore` 忽略。(N5)

## 端到端场景

- [ ] 场景 1（anthropic 多轮）：单条 anthropic 配置启动 → 连续两轮、第二轮引用第一轮 → 流式 + 计时 + markdown 定型 → `/exit` 退出。
- [ ] 场景 2（openai 流式）：openai 协议配置 → 发一条含代码块的请求 → 流式逐字后 markdown 渲染正确。
- [ ] 场景 3（多 provider 选择）：两条配置 → 启动出现列表 → 选第二条 → 状态栏显示其 name/model → 正常对话。
- [ ] 场景 4（错误恢复）：错误 key 触发失败 → 对话区红色错误、程序不退出 → 修正后（重启）继续正常对话。
