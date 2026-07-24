# Mode Switch UI Plan

## 架构概览

改动集中在 TUI 层单一组件 `KoyoCodeApp`（`src/koyocode/tui/app.py`），三处方法 + 一处模块级数据结构：

1. **配色映射数据结构**：新增模块级常量 `_MODE_VISUAL`，集中管理四档 mode 的文案与颜色，替代现有 `_mode_label` 函数。
2. **状态栏渲染**：重写 `_update_statusbar`，用 Rich `Text.assemble` 构造带样式的文本（mode 段染 mode 色、可选提示段染 dim、model/usage 段不染色）后 `update` 到状态栏 `Static`。
3. **Shift+Tab 按键分支**：`on_key` 的 `shift+tab` 分支移除历史区消息追加，末尾显式拉回输入框焦点。
4. **输入框边框副标题**：`on_mount` 移除 border_subtitle 中的「Shift+Tab 切换模式」字样。

不涉及权限引擎、agent loop、provider、工具系统，mode 对权限/工具的实际生效逻辑完全不变。

## 核心数据结构

### `_MODE_VISUAL`（模块级常量）

```python
_MODE_VISUAL: dict[Mode, tuple[str, str]] = {
    Mode.DEFAULT: ("DEFAULT", ""),          # 不染色（默认前景）
    Mode.ACCEPT_EDITS: ("ACCEPT EDITS", "#FFB347"),  # 琥珀黄
    Mode.PLAN: ("PLAN", "#4FC3F7"),         # 青蓝
    Mode.BYPASS: ("BYPASS", "#FF5252"),     # 红
}
_CYCLE_HINT = "(shift+tab to cycle)"
```

- 键为 `permission.Mode`，值为 `(label, color)`。
- `color` 为空字符串表示不染色（默认前景色），传入 `Text.assemble` 的 style 段时即「无样式」。
- 提示文案单独抽常量，便于测试断言与未来调整。

## 核心接口

### `_update_statusbar(self) -> None`（重写）

构造带样式的状态栏文本并 update：

```python
def _update_statusbar(self) -> None:
    if self.provider is None:
        return
    label, color = _MODE_VISUAL[self.mode]
    usage = f"  ↑{_fmt_tokens(self.usage_in)} ↓{_fmt_tokens(self.usage_out)} tok"
    segments: list[tuple[str, str]] = [
        ("● ", color),
        (label, color),
    ]
    if self.mode != Mode.DEFAULT:
        segments.append((f" {_CYCLE_HINT}", "dim"))
    segments.append((f"    {self.provider.model}{usage}", ""))
    self.query_one("#statusbar", Static).update(Text.assemble(*segments))
```

- `● ` 与 `label` 同染 mode 色（DEFAULT 时 color 为空，等价默认前景）。
- 仅当 `mode != DEFAULT` 时追加 ` _CYCLE_HINT`（前导空格与 label 分隔），染 `dim`。
- model + usage 段不染色（空 style）。
- 状态栏 `Static` 保持 `markup=False` 不变：Rich `Text` 对象自带结构化 spans，样式由对象承载，不依赖字符串标记解析，规避 markup 注入风险。

### `on_key(self, event: events.Key) -> None`（shift+tab 分支改写）

```python
if key == "shift+tab" and self.state == SessionState.IDLE:
    event.stop()
    self.mode = next_mode(self.mode)
    self._update_statusbar()
    self.query_one("#input", InputArea).focus()
    return
```

- 移除原 `self._append_history_text(f"● 已切换到 ...", "notice-message")`。
- 末尾新增 `self.query_one("#input", InputArea).focus()` 显式拉回输入框焦点（见技术决策「焦点回归」）。

### `on_mount`（border_subtitle 调整）

```python
self.query_one("#input-wrap").border_subtitle = (
    "Send a message...  (Alt+Enter 换行 · Enter 发送)"
)
```

移除「· Shift+Tab 切换模式」片段，仅保留发送相关提示。

### `_mode_label` 函数

移除。其唯一调用点（shift+tab 打印消息）已删除，状态栏改用 `_MODE_VISUAL`。`next_mode` 保留（切换逻辑不变）。

## 模块设计

### `KoyoCodeApp`（TUI 层）
**职责：** 接管 Shift+Tab 切换的 UI 呈现与输入焦点交互；不打印历史消息。
**对外接口：** 无新增对外接口（内部方法重写）。
**依赖：** `rich.text.Text`、`koyocode.permission.Mode`、Textual `Static` / `InputArea`。

## 模块交互

Shift+Tab 按键数据流（IDLE 态）：

```
用户按 Shift+Tab
  └─ App.on_key 拦截 shift+tab
       ├─ event.stop()                    # 阻止事件继续传递
       ├─ self.mode = next_mode(self.mode) # 切换模式
       ├─ self._update_statusbar()         # 重染状态栏（mode 色 + 可选提示）
       └─ input.focus()                    # 显式拉回输入框焦点
```

不再调用 `_append_history_text`，历史区零副作用。状态栏渲染与输入框焦点回归在同一按键处理内同步完成。

## 文件组织

```
koyo-code/
├── src/koyocode/tui/
│   └── app.py            # 修改：_MODE_VISUAL/_CYCLE_HINT 常量、_update_statusbar 重写、
│                         #       on_key shift+tab 分支、on_mount border_subtitle、移除 _mode_label
└── tests/
    └── test_tui.py       # 修改：新增 shift+tab 不打印消息/焦点回归/提示显隐测试，调整依赖 .content 的断言
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 染色实现 | Rich `Text.assemble` 对象 + 状态栏 `markup=False` | 结构化 spans 承载样式，不依赖字符串标记解析，无 markup 注入风险；`Text.__contains__` 检查 `.plain`，现有 `"DEFAULT" in sb` 类断言无需改动即可继续通过 |
| 焦点回归 | shift+tab 分支末尾显式 `input.focus()` | Textual 默认 shift+tab 会触发焦点回退（focus_previous）/TextArea dedent，`event.stop()` 未必能阻止默认焦点动作；显式 `focus()` 在处理末尾把焦点钉回输入框最可靠。若测试中发现同帧焦点竞争导致 focus 未生效，降级为 `self.call_after_refresh(lambda: self.query_one("#input", InputArea).focus())` 延迟一帧执行 |
| 配色映射 | 模块级 `_MODE_VISUAL` dict 替代 `_mode_label` 函数 | 集中管理 label+color，新增/调整配色只改一处；与 spec 四档配色一一对应 |
| 提示文字显隐 | `mode != DEFAULT` 时拼接 dim 段 | DEFAULT 为默认态无需提示如何切换；进入非默认态后提示用户可继续循环切回，符合 Claude Code 交互直觉 |
| 圆点配色 | `● ` 与 mode 文案同色 | 视觉上圆点+文案作为整体标识当前模式，四档区分更醒目 |

## spec 覆盖核对

| spec 需求 | plan 归属 |
|-----------|-----------|
| F1 不打印历史消息 | `on_key` shift+tab 分支移除 `_append_history_text` |
| F2 mode 文案四档配色 + 圆点同色 | `_MODE_VISUAL` + `_update_statusbar` 的 `Text.assemble` |
| F3 非 DEFAULT 显示 dim 提示 / DEFAULT 不显示 | `_update_statusbar` 的 `if self.mode != Mode.DEFAULT` 分支 |
| F4 边框移除切换提示 | `on_mount` border_subtitle 调整 |
| F5 切换后输入框聚焦、文字不清空 | `on_key` shift+tab 分支末尾 `input.focus()`（不触碰 input.text） |

无缺口。
