# KoyoCode

> 一个 Claude Code 风格的终端 AI 编程助手，用 Python 从零构建。

![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

KoyoCode 是一个运行在终端里的 AI 编程 Agent：你在输入框用自然语言提需求，它会自主读取文件、执行命令、编辑代码，把任务做完。它同时支持 Anthropic 与 OpenAI 两种协议（含各类兼容端点），内置文件编辑与命令执行工具，可接入任意 MCP server 扩展能力，并对每一次有副作用的操作做多层权限防护。

本项目既是一个可用的工具，也是一份"如何从零造一个终端 AI Agent"的实践记录--每一块功能都通过 [koyospec](#koyospecspec-驱动开发) 这套 spec 驱动流程设计、拆解、实现、验收，完整设计文档随仓库公开。

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置](#配置)
- [架构](#架构)
- [项目结构](#项目结构)
- [koyospec：spec 驱动开发](#koyospecspec-驱动开发)
- [开发路线](#开发路线)
- [许可](#许可)

---

## 功能特性

- **多协议 LLM 接入**：一份配置在 Anthropic 与 OpenAI 协议间切换，支持自定义 `base_url` 接入任意兼容端点（DeepSeek、本地模型等）。上层交互与协议无关。
- **ReAct Agent 循环**：模型自主决定调用工具、观察结果、继续推理，循环往复直到完成任务。流式输出、多轮上下文、工具调用全程在终端实时呈现。
- **六款内置工具**：`read_file` / `write_file` / `edit_file` / `bash` / `glob` / `grep`，覆盖读、写、改、执行、查找的完整编码动作。
- **MCP 工具扩展**：通过 [MCP 协议](https://modelcontextprotocol.io/) 接入外部 server（GitHub、数据库、HTTP 服务等），远端工具与内置工具一视同仁，对 Agent 与权限系统透明。
- **五层权限防护**：危险命令黑名单 -> 文件路径沙箱 -> 三级规则引擎 -> 模式兜底 -> 人在回路审批。有副作用的操作默认要你点头才执行。
- **四档权限模式**：`default`（默认审批）/ `acceptEdits`（自动放行文件编辑）/ `plan`（只读规划）/ `bypassPermissions`（全自动，黑名单与沙箱仍生效），Shift+Tab 循环切换。
- **工程化系统提示**：稳定提示块（可缓存）与环境信息块（git 状态、工作目录、日期等）物理分离，兼顾 prompt cache 命中率与上下文新鲜度。
- **全功能 TUI**：基于 Textual 的终端界面，流式逐字渲染、Markdown 美化、工具执行可视化、响应计时、原生文本选区复制。

---

## 快速开始

### 环境要求

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**（推荐，用于依赖管理与运行）或任意现代 Python 包管理器
- 一个可用的 LLM API Key（Anthropic 或 OpenAI 兼容）

### 安装

```bash
git clone https://github.com/koyomi315/koyo-code.git
cd koyo-code
uv sync          # 创建虚拟环境并安装依赖（uv.lock 已入库，可复现构建）
```

### 配置 API Key

复制示例配置并填入你的密钥：

```bash
cp .koyocode/config.example.yaml .koyocode/config.yaml
```

编辑 `.koyocode/config.yaml`：

```yaml
providers:
  - name: Claude (Anthropic)
    protocol: anthropic
    api_key: sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
    model: claude-3-7-sonnet-latest
    thinking: true          # 仅 anthropic 生效，开启扩展思考（思考内容接收即丢弃，不展示）

  # OpenAI / 兼容端点示例：
  # - name: GPT-4o
  #   protocol: openai
  #   api_key: sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
  #   model: gpt-4o
  #   base_url: https://api.openai.com/v1   # 接入兼容端点时填此处
```

> `.koyocode/config.yaml` 含真实密钥，已在 `.gitignore` 中忽略，不会误提交。多于一个 provider 时，启动后会出现方向键选择列表。

### 运行

```bash
uv run koyocode            # 通过注册的命令入口启动
# 或
uv run python -m koyocode  # 通过模块方式启动
```

启动后即进入对话界面，在底部输入框输入需求、按 Enter 提交即可。常用操作：

| 操作 | 按键 |
|---|---|
| 提交输入 | `Enter` |
| 输入框内换行 | `Alt+Enter` |
| 切换权限模式 | `Shift+Tab` |
| 取消当前回复 | `Ctrl+C` / `Esc`（流式或审批态） |
| 退出程序 | `/exit` 或 `Ctrl+C`（空闲态） |
| 进入/退出规划模式 | `/plan` / `/do` |

---

## 配置

KoyoCode 有三套互相独立的 YAML 配置文件，各司其职：

| 用途 | 项目级路径 | 用户级路径 | 是否入库 |
|---|---|---|---|
| **LLM 供应商** | `.koyocode/config.yaml` | - | 否（含密钥，已忽略） |
| **权限规则** | `.koyocode/settings.yaml` | `~/.koyocode/settings.yaml` | 项目级入库，用户级否 |
| **MCP 服务器** | `.koyocode.yaml` | `~/.koyocode/config.yaml` | 示例入库 |

> 注意路径区别：LLM 配置在 `.koyocode/` **目录**内的 `config.yaml`；MCP 的项目级配置是项目根下的 `.koyocode.yaml` **文件**。三层权限配置优先级：`settings.local.yaml`（本地，已忽略）> `settings.yaml`（项目）> `~/.koyocode/settings.yaml`（用户）。

### 权限配置示例

```yaml
# .koyocode/settings.yaml
default_mode: default      # default | acceptEdits | plan | bypassPermissions

permissions:
  allow:
    - "Bash(git *)"        # 放行 git 系列
    - "Bash(pytest)"       # 精确放行 pytest
  deny:
    - "Bash(rm *)"         # 禁止 rm
    - "Read(.env)"         # 围栏：禁止读 .env
    - "Write(.env)"        # 围栏：禁止写 .env
```

规则用友好名（`Bash` / `Read` / `Write` / `Edit` / `Glob` / `Grep`）加括号内 glob 模式。同层 `deny` 优先于 `allow`，越靠近会话越优先。内置危险命令黑名单与路径沙箱始终生效，任何配置都无法放开。

### MCP 配置示例

```yaml
# .koyocode.yaml（项目级）或 ~/.koyocode/config.yaml（用户级）
# 同名 server 项目级完整覆盖用户级；env/headers 支持 ${VAR} 环境变量展开
mcp_servers:
  github:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
  example-http:
    type: http
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${EXAMPLE_TOKEN}"
```

完整示例见 [`docs/ch07/mcp-servers.example.yaml`](docs/ch07/mcp-servers.example.yaml)。MCP 工具注册后以 `mcp__<server>__<tool>` 命名，与内置工具共享同一注册中心。

---

## 架构

KoyoCode 的核心是一条 **ReAct 循环**，围绕它组织了协议无关的 LLM 抽象、工具系统、权限引擎与终端界面。

```
                       ┌─────────────────────────────────────────┐
                       │                  TUI (Textual)          │
                       │   历史 / 流式 / 输入 / 状态栏 / 审批菜单  │
                       └───────────────┬─────────────────────────┘
                                       │ Event 流（text/tool/usage/done…）
                          ┌────────────▼────────────┐
                          │        Agent Loop       │
                          │  流式请求 -> 工具调用 ->   │
                          │  结果回灌 -> 再请求 …     │
                          └──┬──────────┬───────────┘
                             │          │ check()
              ┌──────────────▼──┐   ┌───▼──────────────────────┐
              │   LLM Provider  │   │     权限引擎（五层）       │
              │  anthropic /    │   │ 黑名单->沙箱->规则->模式->审批 │
              │  openai 适配    │   └──────────────────────────┘
              └──────────────┬──┘
                             │ ToolUseBlock
                  ┌──────────▼──────────┐
                  │    工具注册中心      │
                  │  内置工具 │ MCP 工具  │
                  └─────────────────────┘
                             │
                       Conversation（进程内多轮历史）
```

### 核心模块

- **`agent/`** - Agent 循环。以 async generator 吐出 `Event` 事件流，TUI 据此渲染。每轮：装配系统提示 -> 流式请求 LLM -> 若有工具调用则分批执行（只读工具并发、有副作用工具串行）-> 结果回灌 -> 继续下一轮。包含 5 种终止条件（自然完成、迭代上限、用户取消、连续未知工具、流出错），各自干净收尾。
- **`llm/`** - 协议无关的 LLM 抽象。`Provider` Protocol + 统一的 `Message` / `ToolUseBlock` / `ToolResultBlock` / `StreamEvent` 类型，两个适配器封装 SDK 差异（系统提示分块、工具调用流式收集、reminder 注入、缓存用量解析）。
- **`tool/`** - 工具系统。`Tool` Protocol 定义统一接口，`Registry` 负责注册与按序执行。工具永不抛异常，一律以 `ToolResult` 值类型返回。
- **`permission/`** - 五层权限引擎。`Engine.check()` 串联黑名单、沙箱、三级规则引擎、模式兜底四层短路判定，第五层人在回路由 Agent 编排驱动（async Future 等待用户三选一）。
- **`prompt/`** - 系统提示工程。稳定块（7 个固定模块按优先级装配，可缓存）与环境块（git 状态/目录/平台等，不缓存）分离；reminder 每轮动态注入、不写入持久历史。
- **`mcp/`** - MCP 客户端。并发连接外部 server，把远端工具适配为 `McpTool` 注册进同一注册中心，对 Agent 与权限系统完全透明。
- **`tui/`** - Textual 终端界面。流式渲染、工具执行可视化、四态状态机（选择/空闲/流式/审批）、权限模式切换。
- **`conversation.py`** - 进程内单会话多轮历史维护（不持久化）。
- **`config.py`** - LLM 供应商配置加载与校验。

### 关键设计点

1. **协议无关**：上层只面对统一的 `Provider` 与 Block 类型，两套 SDK 的差异全封在适配器里。
2. **降级安全**：权限引擎构造失败返回空规则安全引擎、MCP 连接失败跳过该 server、配置解析失败跳过该文件--单个组件失败不阻断整体启动。
3. **保序分批执行**：连续只读工具并发、有副作用工具串行，保持模型给出的相对顺序。
4. **缓存友好**：系统提示稳定块跨轮逐字节一致以命中 prompt cache；工具结果在第 8 章引入上下文压缩后也会冻结替换字符串以保证缓存稳定。

---

## 项目结构

```
koyo-code/
├── src/koyocode/
│   ├── agent/            # Agent 循环（ReAct 核心）
│   ├── llm/              # LLM Provider 抽象与 anthropic/openai 适配
│   ├── tool/             # 内置工具（read/write/edit/bash/glob/grep）
│   ├── permission/       # 五层权限引擎
│   ├── prompt/           # 系统提示装配与环境采集
│   ├── mcp/              # MCP 客户端（config/manager/tool）
│   ├── tui/              # Textual 终端界面
│   ├── conversation.py   # 对话历史
│   ├── config.py         # 供应商配置加载
│   └── cli.py            # 入口装配
├── tests/                # 单元与集成测试
├── smoke/                # 端到端 smoke 测试
├── examples/             # 示例脚本
├── koyospec/             # spec 驱动开发的设计文档（见下文）
├── docs/ch07/            # MCP 配置示例
├── .koyocode/            # 配置示例（config.example.yaml / settings.yaml.example）
└── pyproject.toml        # 项目元数据与依赖
```

---

## koyospec：spec 驱动开发

本项目使用 **[koyospec](https://github.com/koyomi315/koyo-spec)** 这套 spec 驱动开发方法：每个功能在动手写代码前，先产出"做什么 / 怎么做 / 按什么顺序做 / 做对了没"四份递进设计文档并逐份审批，实现后再按验收清单逐项检查。各需求的设计文档保存在 [`koyospec/`](koyospec/) 目录下，已完成的归档于 `koyospec/archive/`。

---

## 开发路线

项目按"章节"逐步推进，每一章对应一个 koyospec 需求。已完成的需求归档在 `koyospec/archive/`：

| 阶段 | 需求 | 状态 |
|---|---|---|
| ch01–02 | `init-koyocode-core` - 多协议对话 + TUI 基础闭环 | ✅ 已归档 |
| ch03–04 | `tool-system` - 内置工具与注册中心 | ✅ 已归档 |
| ch05 | `agent-loop` - ReAct Agent 循环 | ✅ 已归档 |
| ch06 | `permission-system` - 五层权限防护 | ✅ 已归档 |
| - | `system-prompt` / `mode-switch-ui` - 提示工程化与模式切换 | ✅ 已归档 |
| ch07 | `mcp-system` - MCP 客户端集成 | ✅ 已归档 |
| ch08 | `context-compact` - 上下文管理（两层压缩 + 紧急兜底） | 🚧 进行中 |

后续方向包括长期记忆、Skill 子系统、跨会话持久化等（详见各 spec 的"不做的事"留给后续的条目）。

---

## 许可

本项目采用 **MIT** 协议，详见 [LICENSE](LICENSE)。

---

> KoyoCode 是一个学习与实践性质的项目，参考了 [Claude Code](https://claude.com/claude-code) 的交互理念，从零实现。欢迎交流。
