# MCP 客户端 Checklist

> 每一项通过运行代码或观察行为来验证；函数 / 类型名仅作定位提示，核验断言本身不依赖其命名（重命名实现而行为不变时本清单仍适用）。

## 实现完整性

- [x] 加载两层配置：两文件存在时按 server 名合并、同名 server 项目级完整覆盖用户级（验证：单测构造两层文件断言合并结果与字段来源）。(AC1/F1) 证据：`tests/test_mcp_config.py` 通过；`config.py` `_merge_servers` 项目级 dict 覆盖用户级。
- [x] 配置降级：任一文件缺失视为空、格式非法跳过该文件 + stderr 告警 + 其它正常加载，不致启动失败（验证：单测分别投喂缺失与非法 YAML，断言 `load_config` 不抛异常且其它层 server 仍在）。(AC1/N1) 证据：`_load_file` 捕获 `OSError`/`YAMLError` + stderr 告警 + 返回空；单测通过。
- [x] 字段校验：stdio 缺 command、http 缺 url、`type` 非法或缺失，均跳过该 server + stderr 给出原因，其它 server 不受影响（验证：单测分别构造各非法 server）。(AC2/N2) 证据：`_validate_server` 各分支 print + return None；单测通过。
- [x] `${VAR}` 展开：env / headers 的值被展开；未定义变量展开为空串 + 一次性告警；command / args / 工具名 / server 名不展开（验证：单测覆盖各分支，含 `command: ${X}` 应保留字面量）。(AC3/F3) 证据：`_apply_expansion` 仅遍历 `srv.env`/`srv.headers`；`_expand_vars` 未定义返回 `""` 并收集 undefined，seen 去重告警；command/args 透传未展开；单测通过。
- [x] stdio 连接 + 握手 + 列工具：能拉起一个 MCP server 子进程并由 SDK 完成 `session.initialize()` + `session.list_tools()`；`env` 被注入到子进程环境（验证：用单测脚本启动一个最小 echo MCP server 或 tmux 实跑 `@modelcontextprotocol/server-everything`）。(AC4/F4/F6) 证据：`manager.py` `_connect_one` stdio 分支 `StdioServerParameters(env={**os.environ, **srv.env})` → `session.initialize()` → `list_tools()`；T7 tmux 实跑通过。
- [x] HTTP 连接 + 自定义 headers：能对 HTTP MCP server 完成握手 + 列工具；`headers` 真正出现在每个 HTTP 请求中（验证：用 `pytest-httpx` 或 `httpx.MockTransport` 起一个最小 HTTP 端点 + 注入 `Authorization` 头，断言 server 端收到该头）。(AC5/F5/F6/N6) 证据：`streamablehttp_client(srv.url, headers=srv.headers or None)`；`tests/test_mcp_config.py`/`test_mcp_manager.py` HTTP 路径覆盖；单测通过。
- [x] 工具命名：所有 MCP 工具的 `name` 形如 `mcp__<server>__<tool>`；前缀拼接后含 LLM 工具名禁用字符（非 `[A-Za-z0-9_-]`）的工具被跳过并告警（验证：单测构造含 `.` 的 server 名 / 工具名，断言 `adapt_tool` 返回 `None`）。(AC6/AC7/F8) 证据：`adapt_tool` `full_name = f"mcp__{server}__{t.name}"` + `_VALID_NAME.fullmatch` 不匹配则 stderr + return None；单测通过。
- [x] 命名空间隔离：同一 tool 名在不同 server 互不覆盖；与 6 个内置工具天然不重名（验证：registry 注册后断言全名集合无重复）。Manager 适配阶段按 `full_name` 去重，后到者跳过+告警（`Registry.register` 遇重名抛 `ValueError`，故注册前消化）。(AC7/F8) 证据：`_register_tools` 锁内 `existing` set 去重，重名 print + skip；单测通过。
- [x] 工具适配字段：description 空 → 兜底文案；schema 透传为 `dict[str, Any]`、空 schema 兜底 `{"type": "object"}`；`annotations.readOnlyHint==True` → `read_only is True`，其它（含 None / False）→ `False`（验证：单测覆盖各分支，含 `annotations is None` None-safe）。(AC6/F7) 证据：`adapt_tool` description 兜底、`parameters = dict(schema) if schema else {"type":"object"}`、`read_only = bool(t.annotations and t.annotations.readOnlyHint)`；单测通过。
- [x] 调用结果聚合：`execute` 接收 raw JSON 字符串 `args`，内部 `json.loads` 解析为 dict 后调用 `call_tool`，把远端多个 text content 块按顺序拼成 `content`；非 text 块（image/audio/resource_link/embedded_resource）静默丢弃 + 单 tool 限一次告警（验证：`test_mcp_tool` 注入 stub 返回混合内容块，断言 collected 仅含 text 且告警计数为 1）。(AC6/F7) 证据：`McpTool.execute` json.loads 失败转 `is_error`；遍历 content 块仅收 `TextContent`，非 text `_non_text_warn_once` set 告警一次；单测通过。
- [x] 远端错误映射：远端 `isError==True` 时 `ToolResult.is_error is True`，`content` 仍为远端 text（验证：`test_mcp_tool` 注入 stub 返回 `isError=True` + text 块）。(AC6/F7) 证据：`return ToolResult(content="\n".join(texts), is_error=bool(result.isError))`；单测通过。
- [x] 协议错与超时回灌：`call_tool` 抛异常或 30s `asyncio.wait_for` 超时 → `is_error is True` 且 `content` 含可读错因，Agent Loop 不中断（验证：`test_mcp_tool` 注入 stub 抛异常 / 阻塞至超时，断言 `is_error` 与文案）。(AC9/F7/F10/N5) 证据：`execute` `asyncio.wait_for(timeout=_call_timeout=30.0)`，`TimeoutError`/`Exception` 转 `ToolResult(is_error=True)`；单测通过。
- [x] 启动失败隔离：有 server 连接 / 握手 / 列工具失败时，只跳过它自身，其它 server 与内置工具集照常注册可用（验证：`test_mcp_manager` 用一个失败 server + 一个 stub 成功 server，断言成功 server 工具被注册）。(AC8/F9/N1) 证据：`_connect_one` except 捕获任意错 -> 告警 + `done.set()` 不影响其它 task；`new_manager` 各 server 独立 task；单测通过。
- [x] 30s 启动超时：模拟连接卡住的 server 在（测试中缩短的）超时窗口结束后被跳过，启动不阻塞超过该窗口（验证：`test_mcp_manager` 注入连接 stub `await asyncio.Event().wait()` + `monkeypatch.setattr(manager, "connect_timeout", 0.2)`，断言 `new_manager` 在超时窗口附近返回）。(AC8/F9/N1) 证据：`_wait_handshake` `asyncio.wait_for(done.wait(), timeout=connect_timeout)` + 超时告警；`test_mcp_manager.py` 用缩短的 `connect_timeout` 断言；单测通过。
- [x] 退出干净：`Manager.close()` 通过 `AsyncExitStack.aclose()` 终止所有 stdio 子进程、断开 HTTP 会话；某 session 关闭卡住时 5s 兜底返回不阻塞（验证：`test_mcp_manager` 注入 `__aexit__` 阻塞的 fake 上下文 + 短兜底，断言 `close()` 在兜底时间内返回；tmux 实跑退出后 `ps` 无残留子进程）。(AC10/F11/N7) 证据：`close()` set `_close_event` 通知各 task 退出自身 `async with`（同 task 进出满足 cancel_scope 约束），`close_timeout` 兜底超时则 cancel 未完成 task；实现注记 4c1a9e6 修复了跨 task cancel_scope 问题；T7 tmux 实跑退出 ps 无残留；单测通过。

## 集成

- [x] 权限链路自然命中：无规则时 `readOnlyHint=True` 的 MCP 工具走 Read 兜底（default 直接放行）、其余走 Exec 兜底（default Ask）；allow 规则 `mcp__<server>__*` 命中时直接放行；bypass 模式放行（验证：用 `PermissionEngine` 对 mcp 全名调用断言裁决；tmux 实跑见场景 4）。(AC11/F12/N4) 证据：`permission` 包未改动，`McpTool` 暴露 `read_only` 让 Read 兜底自然命中，全名 `mcp__*` 落 Exec 兜底；`tests/test_permission_engine.py`/`test_mcp_manager.py` 单测通过；T7 tmux 场景通过。
- [x] permission 包零改动：`git diff src/koyocode/permission/` 在 ch07 期间无任何修改（验证：本章结束时核对 diff 范围）。(N4) 证据：`git diff main..HEAD -- src/koyocode/permission/ --stat` 空。
- [x] provider 适配层零改动：`src/koyocode/llm/anthropic_provider.py`、`src/koyocode/llm/openai_provider.py` 无修改（验证：核对 diff）。(AC12/N3) 证据：`git diff main..HEAD` 对这两个文件 stat 空。
- [x] 黑名单 / 沙箱对 MCP 工具自动跳过：MCP 工具调用 `extract_target` 返回 `("", False, False)` → 黑名单层因 `target==""` 不命中、沙箱层因 `is_file is False` 不进入（验证：用 permission 的 `check` 对一次 mcp 全名调用断言不被黑名单/沙箱直接 Deny）。(AC11/F12) 证据：`McpTool.name()` 返回全名、`execute` 非内置工具不落地文件操作；`test_permission_blacklist.py`/`test_permission_sandbox.py` 单测维持；全量 213 测试通过。
- [x] ch01–ch06 不退化：`pytest` 全过，既有用例不需要适配（验证：运行测试套件）。(AC13/N5) 证据：`pytest -q` 213 passed。

## 编译与测试

- [x] `python -m koyocode` 在合法配置下能进 TUI（含 / 不含 mcp 配置两种）。证据：本机实测 `.venv/bin/python -m koyocode` 成功渲染 TUI 欢迎界面（koyoCode v0.1.0 / cwd 行 / 「就绪」提示）；T7 tmux 实跑含 mcp 配置进 TUI 通过。
- [x] `ruff format --check .` 无 diff。证据：`ruff format --check .` → 54 files already formatted。
- [x] `ruff check .` 无告警。证据：`ruff check .` → All checks passed!。
- [x] `pytest` 通过（含 `tests/test_mcp_config.py` / `tests/test_mcp_tool.py` / `tests/test_mcp_manager.py`，以及既有 config / conversation / tool / agent / prompt / permission / tui 单测）。证据：`pytest -q` 213 passed；三个 mcp 专项模块 38 passed。
- [x] `pytest --asyncio-mode=auto tests/test_mcp_manager.py` 无悬挂 task / 死锁、无 `RuntimeWarning: coroutine ... was never awaited`（重点守护 Manager 并发连接、共享状态、close 兜底）。(N7/N8) 证据：`pytest --asyncio-mode=auto tests/test_mcp_manager.py` 6 passed，无告警。
- [x] （可选）`mypy src/koyocode/mcp` 通过。证据：`mypy src/koyocode/mcp` → Success: no issues found in 4 source files。
- [x] 凭据不落盘：配置示例 / 文档 / 测试 fixture 全用 `${VAR}`；`git grep -E '(Bearer|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}'` 在 ch07 期间无命中。(AC14/N6) 证据：ch07 提交范围内唯一命中是 ch01 遗留的 `.koyocode/config.example.yaml` 占位符 `sk-ant-xxx...xxx`（非真实凭据，ch07 未改此文件）；其余文件无 `Bearer`/真凭据命中。

## 端到端场景（tmux 实跑）

- [x] 场景 1（无 MCP 配置）：仓库内不存在 `.koyocode.yaml` 与 `~/.koyocode/config.yaml` 时，koyocode 正常进 TUI；registry 仅含 6 个内置工具；stderr 无 mcp 相关告警。(AC1) 证据：本机实测 `python -m koyocode` 在无 mcp 配置下进 TUI 渲染成功；T7 tmux 实跑通过。
- [x] 场景 2（stdio server 接入）：在 `.koyocode.yaml` 配置 `@modelcontextprotocol/server-everything` 一类真实 server，启动后日志显示 server 连接成功 + 工具数；TUI 中让模型调用其中一个工具（如 echo），default 模式弹人在回路 → 「允许本次」→ 工具结果回灌 → 模型续答。(AC4/AC6/AC11) 证据：T7 tmux 实跑验证通过（提交 4008c79）。
- [x] 场景 3（失败隔离）：配置一个不存在 command 的 server + 一个能跑的 server，启动 stderr 有第一个 server 的失败告警；能跑的 server 工具仍可用、能正常调用。(AC8) 证据：T7 tmux 实跑通过；`test_mcp_manager.py` 失败隔离单测通过。
- [x] 场景 4（永久放行 + 重启）：场景 2 中选「永久允许」→ `.koyocode/settings.local.yaml` 出现对应 `mcp__<server>__<tool>` allow 规则；重启 koyocode 后再调该工具不再弹窗直接执行。(AC11) 证据：T7 tmux 实跑通过（提交 4008c79）。
- [x] 场景 5（凭据展开）：配置 `env: { GITHUB_TOKEN: "${GITHUB_TOKEN}" }`；`unset GITHUB_TOKEN` 启动时 stderr 有 undefined 告警但 server 仍尝试启动（server 自决报错与否）；`export GITHUB_TOKEN=...` 后正常工作。(AC3/AC14) 证据：T7 tmux 实跑通过；`config.py` `_expand_vars` 未定义告警逻辑单测覆盖。
- [x] 场景 6（退出干净）：退出 koyocode（`/exit` 或 Ctrl+C）后 `ps -ef | grep server-everything`（或对应 server 进程名）确认子进程无残留。(AC10) 证据：T7 tmux 实跑退出后 ps 无残留（提交 4008c79）；`test_mcp_manager.py` close 兜底单测通过。
- [x] 场景 7（bypass + 黑名单兜底）：Shift+Tab 切到 bypassPermissions，MCP 工具调用不弹窗；让模型跑内置 `bash` 工具 `rm -rf /` 仍被黑名单拦下、回灌被拒。(AC11/N4) 证据：T7 tmux 实跑通过；`test_permission_blacklist.py` 单测维持。
- [x] 场景 8（HTTP server，可选）：本地起一个最小 HTTP MCP server 或用 `pytest-httpx` mock，配置 http 类型 + `headers: { Authorization: "Bearer ${TOKEN}" }`；启动后工具被注册；调用时 server 端日志可见 Authorization 头。(AC5) 证据：实测（`/tmp/koyocode_sc8_e2e.py`）起真实 uvicorn + FastMCP streamable-http server 子进程（带 AuthCapture middleware 落盘收到的 Authorization），Manager 用 `headers={Authorization: Bearer ${TOKEN}}` 连接 → 工具 `mcp__httpdemo__echo` 被注册 → 调 `echo {"text":"hi-sc8"}` 返回 `hi-sc8`（`is_error=False`）→ server 端落盘文件见 Manager 发出的 `Bearer test-secret-xyz`（`${TOKEN}` 正确展开）。
