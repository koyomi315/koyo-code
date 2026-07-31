# UI Polish Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/koyocode/prompt/__init__.py` | 删 `CAT_BANNER`，新增 `LOGO_FONT` 点阵 + `_render_logo`，`render_banner` 改返回 `rich.Text` |
| 修改 | `src/koyocode/tui/app.py` | banner 挂载适配、历史区配色符号/分隔、流式优化、工具折叠、自动滚动修复、完成提示 |
| 修改 | `tests/`（新增/对应测试文件） | logo 渲染、配色符号、参数折叠、完成提示、滚动等行为测试 |
| 不动 | `src/koyocode/tui/view.py` | 死代码，范围外 |

> 说明：任务中「行号」仅为定位参照，下次编辑后即过期，以方法名/符号为准。

---

## T1: banner 点阵 logo 与 `render_banner` 重写 ✅

**文件：** `src/koyocode/prompt/__init__.py`
**依赖：** 无

**步骤：**

1. 删除模块常量 `CAT_BANNER`（猫咪 ASCII）。
2. 新增点阵字体表 `LOGO_FONT: dict[str, list[str]]`，含 K/O/Y/C/D/E 六字母，每字母 3 列 5 行的 `1`/`0` 位图（用用户定稿数据）。
3. 新增常量：`WHALE_BLUE = "#2496ED"`、`LOGO_TEXT = "KOYOCODE"`、`_PIXEL_ON = "  "`。
4. 新增私有函数 `_render_logo(text: str) -> Text`：遍历 5 行；每行对 `text` 中每个字母取 `LOGO_FONT[char][row]`，逐像素 append 双空格--`"1"` 着 `Style(bgcolor=WHALE_BLUE)`，`"0"` 用普通双空格；字母间 append 一个普通空格作列间隔；行末 append `\n`。
5. 重写 `render_banner(version: str, cwd: str) -> Text`：组装 `_render_logo(LOGO_TEXT)` + 空行 + 功能性头部（应用名版本行 `KoyoCode v{version}`、cwd 行、按键提示行），头部用 `Text.append` 带样式（粗体应用名、暗淡 cwd/提示）。返回单个 `rich.Text`。
6. 改写 `READY_HINT` 为按键提示文本常量（发送/换行/模式切换/退出），供头部引用。
7. 更新 `__all__`：移除 `CAT_BANNER`。

**验证：**
- `uv run python -c "from koyocode.prompt import render_banner; t=render_banner('0.1.0','/tmp'); print(type(t)); print(len(t.spans))"` 期望输出类型为 `rich.text.Text` 且 spans 非空。
- 新增/更新单元测试：断言 `render_banner` 返回 `Text`，含 `LOGO_TEXT` 对应像素 span、含版本号与 cwd 文本。

---

## T2: app 适配 banner 返回 `rich.Text` + 新增 `_append_history_rich` ✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T1

**步骤：**

1. 新增方法 `_append_history_rich(self, text: Text, classes: str = "") -> Static`：与 `_append_history_text` 同构，但用 `Static(text, classes=..., markup=False)` 挂载富文本（保留 `history-message` 基类 + 传入 classes）。
2. `on_mount` 中 banner 挂载改用 `_append_history_rich(render_banner(...), "banner-text")`（原 `_append_history_text` 因返回类型变 Text 而改用此法）。
3. 顶部 import 增加 `from rich.text import Text`（如未有）。

**验证：**
- 应用能正常启动并显示 banner（含蓝色 logo 像素 + 头部文本），不报错。
- 单元测试：mock 启动，断言 banner 通过 `_append_history_rich` 挂载、logo Static 含 `banner-text` 类。

---

## T3: 用户/助手界限与历史区配色符号统一 ✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T2

**步骤：**

1. 用户消息符号改为 `❯`：`submit` 中用户消息挂载由 `● {text}` 改为 `❯ {text}`（保持 `user-message` 类，粗体）。
2. 助手消息保持 `●` 青色（`_append_assistant_message` 中 `assistant-marker` 不变）。
3. 耗时行 `  √ {elapsed}s` 改为 `  √ {elapsed}s`（保持 `elapsed-line` 暗淡），符号不变仅确认配色。
4. 错误消息 `● {err}` 改为 `● {err}`（保持 `error-message` 红色粗体）。
5. CSS 中确认各角色类样式：`.user-message`（粗体默认前景）、`.assistant-marker`（青色）、`.tool-line`（青色粗体）、`.tool-result`（暗淡）、`.tool-error`（红粗体）、`.elapsed-line`（暗淡）一致，无冲突。
6. `/plan`、`/do` 命令的历史区反馈（`notice-message`）符号统一为 `●`，保持暗淡。

**验证：**
- 单元测试：提交用户消息后，历史区 widget 文本以 `❯` 开头；助手回复以 `●` 开头。
- 端到端：启动→发一句→观察用户行 `❯`、助手行 `●` 青色，可一眼区分。

---

## T4: 回合间分隔 ✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T3

**步骤：**

1. 新增实例属性 `self._turn_count: int = 0`（在 `__init__` 初始化）。
2. `_start_turn` 开头：若 `self._turn_count > 0`，先 `_append_history_text("─" * 分隔长度, "turn-separator")`；分隔长度取历史区宽度或固定值（如 40），用暗淡细线。`_turn_count += 1`。
3. CSS 新增 `.turn-separator { color: $text-muted; }`。
4. 首轮（`_turn_count == 0`）不加分隔，避免 banner 后紧跟分隔。

**验证：**
- 单元测试：完成首轮后再提交，历史区出现 `turn-separator` 文本；首轮无分隔。
- 端到端：连续两轮对话，可见两轮之间有暗淡细线分隔。

---

## T5: 自动滚动修复 ✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T2

**步骤：**

1. `_append_history_widget` 中 `call_after_refresh(self._scroll_history_end, history)` 改为双层：第一层回调内再 `call_after_refresh(self._scroll_history_end, history)`，给 Markdown 异步展开留时间。
2. `_finish_turn` 中挂载助手回复后，额外 `self.call_after_refresh(self._scroll_history_end, self._history())` 二次确认滚动到底。
3. 流式期间 `_render_streaming` 更新 `#streaming` 区后，无需额外滚动（streaming 区在历史区外，独立）。
4. 验证 `scroll_end(animate=False, immediate=True, x_axis=False)` 调用不变。

**验证：**
- 单元测试（模拟挂载 Markdown 回复 + refresh）：断言 `_scroll_history_end` 被调用、历史区 `scroll_y` 到底。
- 端到端：长回复生成完成后，历史区自动滚到底，最新回复完整可见，无需手动滚动（对照现状修复）。

---

## T6: 流式动态区优化（旋转指示符 + 简洁计时）✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T2

**步骤：**

1. 新增模块常量 `_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"`。
2. 新增实例属性 `self._spinner_frame: int = 0`（`__init__`）。
3. `_tick` 中：若 `state == STREAMING`，`self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)`，再 `_render_streaming`。
4. 重写 `_render_streaming`：
   - 有工具：`{spinner} {name}({args}) · {elapsed}s`（去掉 `Running...`）。
   - 无工具、有回复：`{cur_reply}\n{spinner} {elapsed}s`（去掉 `Imagining...`，用 spinner 表达进行中）。
   - 无工具、无回复：`{spinner} {elapsed}s`（首增量前）。
   - 轮次提示 `· 第 N 轮` 保留在无工具分支末尾。
5. 待批准态 `_render_approving` 不受影响（仍用 `●` 前缀，与流式区分）。

**验证：**
- 单元测试：`_render_streaming` 在有/无工具/有/无回复各状态下输出含 spinner 字符、含 elapsed、无 `Imagining`/`Running` 字样。
- 端到端：生成中动态区显示旋转字符 + 简洁计时，无冗余文字。

---

## T7: 工具行参数折叠与结果摘要 ✅

**文件：** `src/koyocode/tui/app.py`
**依赖：** T3

**步骤：**

1. 新增函数 `_fold_args(args: str, limit: int = 60) -> str`：`len(args) > limit` 时截断为 `args[:limit] + "…"`，否则原样。
2. `_on_tool_end` 中工具行 args 改用 `_fold_args(args)`：`_append_history_text(f"● {name}({_fold_args(args)})", "tool-line")`。
3. `_tool_result_text` 保持 `└` 前缀 + 4 空格缩进 + 截断（`_TOOL_RESULT_MAX_LINES` 不变），仅确认格式。
4. 错误结果走 `tool-error` 类（红色粗体），正常走 `tool-result`（暗淡）--已有逻辑，确认不变。

**验证：**
- 单元测试：`_fold_args("x"*100)` 返回长度 61（60+`…`）；`_fold_args("short")` 原样。
- 端到端：触发一个长参数工具调用，工具行参数被折叠；结果摘要 `└` 缩进截断清晰。

---

## T8: 状态栏完成提示（闪现 ✓ 完成 2s）

**文件：** `src/koyocode/tui/app.py`
**依赖：** T2

**步骤：**

1. 新增实例属性 `self._done_feedback_until: float | None = None`、`self._done_timer: Timer | None = None`（`__init__`）。
2. `_update_statusbar` 增加完成态分支：`if self._done_feedback_until is not None and time.monotonic() < self._done_feedback_until:` 渲染 `Text("✓ 完成 · {elapsed}s", style="green")`；否则走原常规渲染。
3. 新增 `_flash_done(self, elapsed_s: int)`：`self._done_feedback_until = time.monotonic() + 2.0`；停掉旧 `_done_timer`；`self._done_timer = self.set_timer(2.0, self._clear_done)`；`_update_statusbar()`。
4. 新增 `_clear_done(self)`：`self._done_feedback_until = None`；`self._done_timer = None`；`_update_statusbar()`。
5. `_finish_turn` 中（渲染最终回复后）调用 `self._flash_done(elapsed)`。
6. `_finish_with_error` 不触发完成提示（错误态不闪现 ✓）。
7. `_quit` 中停掉 `_done_timer`（与 `_timer`/`_copy_feedback_timer` 同处理）。

**验证：**
- 单元测试：`_flash_done` 后 `_update_statusbar` 输出含 `✓ 完成`；2s 后（mock timer）`_clear_done` 恢复常规内容含 model。
- 端到端：一轮正常生成完成后，状态栏闪现绿色「✓ 完成 · Xs」约 2 秒，随后恢复模式/model/token。

---

## T9: 测试补全与 lint/类型全量校验

**文件：** `tests/`、全项目
**依赖：** T1–T8

**步骤：**

1. 汇总 T1–T8 各任务新增/更新的测试，确认覆盖：logo 渲染、banner 返回 Text、用户/助手符号、回合分隔、参数折叠、完成提示、滚动调用。
2. 运行 `uv run ruff check .`，修复所有告警。
3. 运行 `uv run mypy src/koyocode`，修复类型错误。
4. 运行 `uv run pytest -q`，确认全绿。

**验证：**
- ruff 无告警、mypy 无错误、pytest 全绿。
- 无遗留 TODO/占位。

---

## 执行顺序

```
T1 (banner/logo) -> T2 (适配+rich挂载) -> T3 (配色符号) -> T4 (回合分隔)
                                        -> T5 (自动滚动) -> T6 (流式优化)
                                        -> T7 (工具折叠) -> T8 (完成提示)
T1..T8 全部完成 -> T9 (测试+lint+类型全量校验)
```

T3–T8 在 T2 之后可按序或部分并行（均依赖 T2 的 `_append_history_rich` 与适配，但彼此改动方法组不重叠）。T9 收尾。
