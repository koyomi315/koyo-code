# Mode Switch UI Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为而非实现细节。

## 实现完整性

- [ ] 状态栏 mode 文案按四档配色渲染已实现（验证：运行 `test_status_bar_shows_current_mode_no_provider_name`，各模式文案断言通过）
- [ ] shift+tab 切换不再向历史区打印消息（验证：运行 `test_shift_tab_does_not_append_history_message` 通过）
- [ ] shift+tab 切换后输入框焦点回归（验证：运行 `test_shift_tab_keeps_input_focused` 通过）

## 集成（对应 spec 验收标准）

- [ ] **AC1**：IDLE 态按 Shift+Tab 循环切换模式时，对话历史区不出现「已切换到 xxx 模式」类消息（验证：运行 `test_shift_tab_does_not_append_history_message`，遍历 `#history` 下 Static 文本无「已切换到」）
- [ ] **AC2**：状态栏 mode 文案颜色随模式变化--ACCEPT_EDITS 琥珀黄、PLAN 青蓝、BYPASS 红、DEFAULT 默认前景色，前置 `●` 圆点与文案同色（验证：启动应用目视确认四档配色；运行 `test_status_bar_shows_current_mode_no_provider_name` 确认各模式文案存在）
- [ ] **AC3**：非 DEFAULT 模式时状态栏 mode 文案后紧邻显示灰色 `(shift+tab to cycle)`；回到 DEFAULT 时该提示消失（验证：运行 `test_statusbar_shows_cycle_hint_only_in_non_default`，DEFAULT 不含提示、ACCEPT_EDITS 含提示、循环回 DEFAULT 再不含）
- [ ] **AC4**：输入框边框副标题不含「Shift+Tab」字样，仍保留「Alt+Enter 换行 · Enter 发送」类发送提示（验证：运行 `test_border_subtitle_has_no_shift_tab_hint`）
- [ ] **AC5**：输入框输入部分文字后按 Shift+Tab，切换后输入框仍聚焦（光标可见）、已输入文字保留（验证：运行 `test_shift_tab_keeps_input_focused`，断言 `app.focused is input` 且 `inp.text` 保留）
- [ ] **AC6**：Shift+Tab 在 STREAMING / APPROVING / SELECTING 态按下不触发 mode 切换（验证：运行 `test_shift_tab_cycles_modes` 确认仅 IDLE 态循环；在流式过程中按 Shift+Tab，观察 `app.mode` 不变）

## 编译与测试

- [ ] 代码格式无 diff（验证：`.venv/bin/ruff format --check src tests`）
- [ ] Lint 无 error/warning（验证：`.venv/bin/ruff check src tests`）
- [ ] 类型检查无 error（验证：`.venv/bin/mypy src`）
- [ ] 全量测试通过，含 4 个新增测试（验证：`.venv/bin/python -m pytest`）

## 端到端场景

- [ ] **场景 1（完整循环）**：启动 KoyoCode 进入单 provider IDLE 态，连续按 Shift+Tab，观察三点同时成立--① 对话历史区不出现切换模式的消息刷屏；② 左下角状态栏在 DEFAULT（无配色、无提示）→ ACCEPT_EDITS（琥珀黄 + 灰色 `shift+tab to cycle`）→ PLAN（青蓝 + 提示）→ BYPASS（红 + 提示）→ DEFAULT（无配色、无提示）之间循环；③ 每次切换后输入框光标始终可见、可继续输入（验证：Windows Terminal + PowerShell 启动 `koyocode`，手动操作目视确认）
- [ ] **场景 2（输入不中断）**：输入框输入「草稿文字」后按 Shift+Tab 切到 ACCEPT_EDITS，再继续键入「追加」--观察「草稿文字」仍在、光标停在输入框内、可正常追加字符（验证：手动操作，或 pilot 测试断言切换前后 `inp.text` 保留且 focus 不丢）
