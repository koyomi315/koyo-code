# UI Polish Plan

## 架构概览

本次改造全部落在 TUI 呈现层，不触碰 `agent/`、`permission/`、`llm/`、`tool/`、`conversation.py` 等业务逻辑。涉及三个文件：

- **`prompt/__init__.py`**：重写 banner 拼装。新增 logo 点阵数据与渲染函数，输出 `rich.Text`（带真彩色背景 span 的像素方块）+ 功能性头部文本。
- **`tui/app.py`**：核心改动文件。涵盖 banner 挂载、历史区配色符号统一、用户/助手界限、流式动态区优化、工具行与结果摘要、回合分隔、自动滚动修复、完成提示。
- **`tui/view.py`**：旧 Rich 渲染辅助，当前已不被 app 引用（注释标注为历史参考）。本次保持不动，不纳入改动范围。

整体仍是单 `KoyoCodeApp(App)`，以 `SessionState` 状态机驱动。改动集中在「历史区如何 append」「状态栏如何渲染」「流式区如何渲染」「滚动时机」四个面，不改变状态机本身与事件分派逻辑。

### 关键技术验证结论（已实证）

1. **Logo 真彩色背景可行**：`rich.Text` + `Style(bgcolor="#2496ED")` 的双空格 span 能被 `Static` 经 `Content.from_rich_text` 正确渲染为真彩色背景方块。Logo 不依赖 ANSI 转义字符串，改用 `rich.Text` 拼接 span，Textual 原生支持。
2. **Markdown 异步 reflow 是滚动根因**：`call_after_refresh` 在 Markdown 展开前触发，导致 `scroll_end` 时机过早。需在内容稳定后二次滚动。

## 核心数据结构

### 点阵字体表 `LOGO_FONT`

```python
LOGO_FONT: dict[str, list[str]] = {
    "K": ["101","110","100","110","101"],
    "O": ["111","101","101","101","111"],
    "Y": ["101","101","010","010","010"],
    "C": ["111","100","100","100","111"],
    "D": ["110","101","101","101","110"],
    "E": ["111","100","110","100","111"],
}
```

每个字母为 3 列 × 5 行的 `1`/`0` 位图字符串列表。`1` 表示该像素点亮（填充背景色），`0` 表示空白。仅含 K/O/Y/C/D/E 六个字母，覆盖 `KOYOCODE`。

### 像素与配色常量

```python
WHALE_BLUE = "#2496ED"          # 鲸鱼蓝（Docker 蓝），logo 像素背景色
LOGO_TEXT = "KOYOCODE"
_PIXEL_ON = "  "                 # 两个空格组成近似正方形像素
```

### banner 头部文本常量

功能性头部各行的文本与样式（粗体应用名、暗淡 cwd、暗淡按键提示），由 `render_banner` 组装。

### 状态栏完成态标志

```python
# app 实例属性
self._done_feedback_until: float | None = None  # 完成提示截止时刻（monotonic）；None 表示非完成态
```

用截止时刻而非布尔，便于 `_tick` 与 `_update_statusbar` 判断是否仍处于完成态、何时恢复。

## 模块设计

### 模块 A：banner 拼装（`prompt/__init__.py`）

**职责：** 产出启动 banner 的 `rich.Text`（logo）+ 多行文本（功能性头部），供 app 挂到历史区顶部。

**对外接口：**

```python
def render_banner(version: str, cwd: str) -> Text:
    """返回 banner：logo（rich.Text，真彩色背景像素）+ 头部多行文本。

    返回单个 rich.Text，内含换行，logo 各行像素以 Style(bgcolor=...) 着色，
    头部各行以普通文本 + 样式（粗体/暗淡）呈现。
    """
```

- 内部私有函数 `_render_logo(text: str) -> Text`：遍历 5 行 × N 字母，每像素 append `_PIXEL_ON`（着 `WHALE_BLUE` 背景）或两个普通空格，字母间留一列空白。
- `CAT_BANNER` / `READY_HINT` 常量：`CAT_BANNER` 移除；`READY_HINT` 保留并改写为按键提示文本。
- `render_banner` 返回类型由 `str` 改为 `Text`（breaking，但唯一调用方在 `app.on_mount`，同步适配）。

**依赖：** `rich.text.Text`、`rich.style.Style`、`LOGO_FONT`。

### 模块 B：历史区渲染（`tui/app.py` 历史区方法组）

**职责：** 用户/助手/工具/结果/错误/耗时各角色的挂载与配色符号统一；回合分隔；自动滚动修复。

**对外接口（内部方法，签名变化）：**

- `_append_history_text(text, classes="")`：现有签名不变，用于暗淡文本（耗时、通知、分隔）。
- 新增 `_append_history_rich(text: Text, classes: str = "")`：挂载带 `rich.Text` 内容（着色 span）的 Static，供 logo 等富文本使用。
- `_append_assistant_message(reply, elapsed_s)`：现有方法，调整圆点符号与配色（见配色表）。

**用户/助手界限配色表（核心决策）：**

| 角色 | 符号前缀 | 颜色/样式 | 文本 |
|------|---------|----------|------|
| 用户 query | `>` 或 `❯` | 默认前景 + 粗体 | 原文 |
| 助手回复 | `●` | 青色 | Markdown |
| 耗时 | `√ {s}s` | 暗淡 | 行尾 |
| 工具行 | `● name(args)` | 青色粗体 | 折叠参数 |
| 工具结果 | `└` | 暗淡/错误红 | 截断 |
| 错误 | `●` | 红色粗体 | 异常文本 |
| 回合分隔 | 空行或 `─` 细线 | 暗淡 | 分隔 |

用户与助手用**不同符号**（`❯` vs `●`）+ 不同色重区分，解决「看不出哪个是 query」的问题。具体符号待 plan 确认后定稿（见技术决策）。

**回合分隔：** 每轮 `_start_turn` 开始前（非首轮）追加一行暗淡分隔（细线 `─────` 或空行），使每轮成组。

**自动滚动修复：**

- 现状：`_append_history_widget` 用 `call_after_refresh(scroll_end)`，Markdown 异步展开后滚动过早。
- 改法：保留 `call_after_refresh` 作首轮滚动；在 `_finish_turn`（最终回复挂载）后，额外用 `self.watch`/`on_idle` 或再叠一层 `call_after_refresh`，在 Markdown `document` 更新完成后二次 `scroll_end`。
- 实现倾向：用 Markdown 的 `MarkdownUpdated` 消息或 `on_idle` 钩子触发最终滚动，保证内容完全展开后到底。

### 模块 C：流式动态区（`tui/app.py` 流式方法组）

**职责：** 优化 `Imagining`/`Running` 的措辞与计时。

**对外接口（内部方法）：**

- `_render_streaming()`：现有方法，重写视图文案。
  - 无工具、有回复：`{cur_reply}\n● {elapsed}s` 旋转指示符（`⠋⠙⠹...` 循环，按 `_tick` 推进帧）。
  - 无工具、无回复：`● {elapsed}s` + 旋转指示符（首增量前）。
  - 有工具：`● name(args) · {elapsed}s`（去掉冗余 `Running...`，旋转指示符表达进行中）。
- 旋转帧索引由 `_tick` 维护（新增 `self._spinner_frame: int`，每 tick 递增取模）。

**依赖：** `time.monotonic`、`self.cur_tools`、`self.cur_reply`、`self.iter`。

### 模块 D：工具行与结果摘要（`tui/app.py` 工具方法组）

**职责：** 工具行参数折叠、结果摘要缩进截断。

**对外接口（内部方法）：**

- `_on_tool_end(name, args, result, is_error)`：现有方法，工具行 `args` 超长时折叠（如超 60 字符截断 + `…`）。
- `_tool_result_text(result)`：现有方法，调整截断行数与缩进（保持 `└` 前缀 + 4 空格缩进）。
- 新增 `_fold_args(args: str, limit: int = 60) -> str`：参数折叠工具函数。

### 模块 E：状态栏与完成提示（`tui/app.py` 状态栏方法组）

**职责：** 常规状态栏渲染 + 完成态闪现。

**对外接口（内部方法）：**

- `_update_statusbar()`：现有方法。增加完成态分支：若 `time.monotonic() < self._done_feedback_until`，渲染 `✓ 完成 · {elapsed}s`（绿色），否则渲染常规模式/model/token。
- 新增 `_flash_done(elapsed_s: int)`：设置 `_done_feedback_until = monotonic() + 2.0`，立即 `_update_statusbar()`，并用 `set_timer(2.0, _clear_done)` 恢复常规。
- 新增 `_clear_done()`：`_done_feedback_until = None`，`_update_statusbar()`。
- `_tick` 每 0.1s 触发时，若处于完成态也调用 `_update_statusbar`（刷新 elapsed，但不提前清除——由 timer 兜底）。

**调用点：** `_finish_turn` 中调用 `_flash_done(elapsed)`。

## 模块交互

1. **启动**：`on_mount` → `render_banner(version, cwd)` 返回 `rich.Text` → `_append_history_rich(banner_text, "banner-text")` 挂载历史区顶部（含 logo 真彩色像素 + 头部文本）。
2. **用户提交**：`submit` → `_append_history_text("❯ {text}", "user-message")`（用户符号）→ `_start_turn` → 非首轮先追加回合分隔。
3. **流式**：`_consume_agent_events` 收事件 → `_render_streaming`（旋转指示符 + 简洁计时）→ `#streaming` 区更新。
4. **工具结束**：`_on_tool_end` → `_fold_args` 折叠参数 → 挂载工具行 + 结果摘要到历史区 → 滚动。
5. **完成**：`done` → `_finish_turn` → 挂载助手回复（青色 `●` + Markdown）+ 耗时 → `_scroll_history_end`（二次确认展开后）→ `_flash_done`（状态栏闪现 ✓ 完成 2s）→ 恢复 IDLE。
6. **状态栏**：`_tick` 持续刷新；完成态期间 `_update_statusbar` 显示 ✓ 完成，2s 后 timer 触发 `_clear_done` 恢复常规。

## 文件组织

```
koyocode/
├── prompt/
│   └── __init__.py        - banner 拼装：LOGO_FONT 点阵、_render_logo、render_banner(返回 Text)
└── tui/
    ├── app.py             - 核心改动：banner 挂载、历史区配色/符号/分隔、流式优化、工具折叠、自动滚动修复、完成提示
    └── view.py            - 不动（旧 Rich 辅助，已不被引用）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Logo 着色方式 | `rich.Text` + `Style(bgcolor=...)` span | 实证 Textual Static 经 `Content.from_rich_text` 支持真彩色背景 span；比 ANSI 转义字符串可靠（Static 的 markup=False 对 ANSI 码处理不稳定） |
| Logo 像素单元 | 两个空格 + 背景色 | 等宽字体下双空格近似正方形；与用户定稿方案一致 |
| 用户/助手区分符号 | 用户 `❯`、助手 `●` | 不同符号 + 不同色重，回看可一眼分辨；`❯` 与输入框 prompt 一致强化「用户侧」语义 |
| 自动滚动修复 | 完成后二次 `scroll_end`（on_idle / call_after_refresh 叠层） | `call_after_refresh` 单次时机过早；二次确认保证 Markdown 展开后到底 |
| 完成提示 | 状态栏闪现 2s + timer 恢复 | 复用状态栏，不新增 widget；timer 兜底恢复，避免被 tick 覆盖 |
| 旋转指示符 | Braille 字符 `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` 循环 | 终端通用 spinner，轻量动效，替代 `Running...` 冗余文字 |
| 工具参数折叠 | 超 60 字符截断 + `…` | 避免长参数撑爆行宽；阈值可调 |
| view.py | 不动 | 已是死代码，本次范围外；避免无关改动 |
| 回合分隔 | 暗淡细线 `─`（终端宽度自适应截断） | 比空行更明确分组，暗淡不喧宾夺主 |
