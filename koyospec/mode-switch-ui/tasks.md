# Mode Switch UI Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/koyocode/tui/app.py` | 配色常量、_update_statusbar 重写、on_key shift+tab 分支、on_mount border_subtitle、移除 _mode_label |
| 修改 | `tests/test_tui.py` | 新增 shift+tab 不打印消息/焦点回归/提示显隐/边框提示测试 |

## T1: 新增配色常量与 Text 导入 [x]

**文件：** `src/koyocode/tui/app.py`
**依赖：** 无
**步骤：**

1. 在 import 区（约 app.py:26-42 附近）新增 `from rich.text import Text`。
2. 在 `_mode_label` 函数定义之前（约 app.py:64 前）新增模块级常量：

   ```python
   _MODE_VISUAL: dict[Mode, tuple[str, str]] = {
       Mode.DEFAULT: ("DEFAULT", ""),                   # 不染色（默认前景）
       Mode.ACCEPT_EDITS: ("ACCEPT EDITS", "#FFB347"),   # 琥珀黄
       Mode.PLAN: ("PLAN", "#4FC3F7"),                   # 青蓝
       Mode.BYPASS: ("BYPASS", "#FF5252"),               # 红
   }
   _CYCLE_HINT = "(shift+tab to cycle)"
   ```

3. 确认 `Mode` 已在 app.py:40 导入（`from koyocode.permission import Mode, Outcome`）。

**验证：** 运行 `.venv/bin/python -c "from koyocode.tui.app import _MODE_VISUAL, _CYCLE_HINT; print(_MODE_VISUAL[Mode.ACCEPT_EDITS], _CYCLE_HINT)"`，期望无 ImportError 并打印 `('ACCEPT EDITS', '#FFB347') (shift+tab to cycle)`。

## T2: 重写 _update_statusbar 用 Text.assemble 染色 [x]

**文件：** `src/koyocode/tui/app.py`
**依赖：** T1
**步骤：**

1. 将 `_update_statusbar`（约 app.py:237-244）整体替换为：

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

2. 移除原 `mode_label = _mode_label(self.mode)` 局部变量行（已被 `_MODE_VISUAL` 取代）。

**验证：** 运行 `.venv/bin/python -m pytest tests/test_tui.py::test_status_bar_shows_current_mode_no_provider_name tests/test_tui.py::test_statusbar_text_is_selectable -x`，期望两个现有测试仍通过（`Text.__contains__` 检查 `.plain`，`"DEFAULT" in sb` 等断言不破坏）。

## T3: on_key shift+tab 分支移除打印消息、加焦点回归、删 _mode_label [x]

**文件：** `src/koyocode/tui/app.py`
**依赖：** T2
**步骤：**

1. 将 `on_key` 的 shift+tab 分支（约 app.py:575-580）替换为：

   ```python
   if key == "shift+tab" and self.state == SessionState.IDLE:
       event.stop()
       self.mode = next_mode(self.mode)
       self._update_statusbar()
       self.query_one("#input", InputArea).focus()
       return
   ```

2. 移除原 `self._append_history_text(f"● 已切换到 {_mode_label(self.mode)} 模式", "notice-message")` 一行。
3. 此时 `_mode_label` 已无任何调用点（T2 改 `_update_statusbar` 后本分支是最后一处），删除 `_mode_label` 函数定义（约 app.py:64-72）。

**验证：** 运行 `.venv/bin/python -c "import koyocode.tui.app as m; assert not hasattr(m, '_mode_label'); print('ok')"`，期望打印 `ok`（_mode_label 已删且无残留引用导致导入失败）。

## T4: on_mount border_subtitle 移除切换提示 [x]

**文件：** `src/koyocode/tui/app.py`
**依赖：** 无（可与 T1-T3 并行）
**步骤：**

1. 将 `on_mount` 中 border_subtitle（约 app.py:206-208）替换为：

   ```python
   self.query_one(
       "#input-wrap"
   ).border_subtitle = "Send a message...  (Alt+Enter 换行 · Enter 发送)"
   ```

2. 确认移除了原句尾「· Shift+Tab 切换模式」片段，保留换行与发送提示。

**验证：** 运行 `grep -n "Shift+Tab" src/koyocode/tui/app.py`，期望无输出（切换提示字样已从边框副标题移除；注释行若有提及可忽略）。

## T5: 新增与调整测试 [x]

**文件：** `tests/test_tui.py`
**依赖：** T2、T3、T4
**步骤：**

1. 新增 `test_shift_tab_does_not_append_history_message`：单 provider 进 IDLE，连按 4 次 `shift+tab` 循环一遍，遍历 `#history` 下所有 `Static` 子 widget 的文本（`widget.content.plain` 或 `str(widget.content)`），断言无任一段包含「已切换到」。
2. 新增 `test_shift_tab_keeps_input_focused`：`input.text = "draft"` 后 `await pilot.press("shift+tab")`，断言 `app.focused is app.query_one("#input", appmod.InputArea)` 且 `inp.text == "draft"`（焦点回归、文字不清空）。若直接 focus 同帧不生效，将实现降级为 `call_after_refresh` 后再断言。
3. 新增 `test_statusbar_shows_cycle_hint_only_in_non_default`：DEFAULT 时断言 `app.query_one("#statusbar", Static).content.plain` 不含 `appmod._CYCLE_HINT`；按一次 `shift+tab` 切到 ACCEPT_EDITS 后断言含 `appmod._CYCLE_HINT`；再按三次回到 DEFAULT 后断言再次不含。
4. 新增 `test_border_subtitle_has_no_shift_tab_hint`：断言 `app.query_one("#input-wrap").border_subtitle` 不含 `"Shift+Tab"` 且含 `"Enter 发送"`。

**验证：** 运行 `.venv/bin/python -m pytest tests/test_tui.py -x`，期望全部通过（含 4 个新增与所有现有测试）。

## T6: 全量校验 [x]

**文件：** 全项目
**依赖：** T5
**步骤：**

1. 格式化：`.venv/bin/ruff format src tests`
2. Lint：`.venv/bin/ruff check src tests`
3. 类型检查：`.venv/bin/mypy src`
4. 全量测试：`.venv/bin/python -m pytest`

**验证：**
- `ruff format --check src tests` 无 diff
- `ruff check src tests` 无 error/warning
- `mypy src` 无 error
- `pytest` 全绿（既有测试 + 新增测试）

## 执行顺序

```
T1 -> T2 -> T3
            ↘
T4（可并行）-> T5 -> T6
```

T4 与 T1-T3 无依赖，可并行；T5 需 T2/T3/T4 全部就绪后才能测全行为；T6 为最终验收。
