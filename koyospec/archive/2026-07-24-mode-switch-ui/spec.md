# Mode Switch UI Spec

## 背景

KoyoCode 已实现四档权限模式（DEFAULT / ACCEPT_EDITS / PLAN / BYPASS）的 Shift+Tab 循环切换，切换仅在 IDLE 态生效、跨轮保持。当前切换交互存在三处体验问题：

1. **切换时在对话历史区打印消息**：每次 Shift+Tab 都会追加「● 已切换到 xxx 模式」到历史区，污染对话记录。Claude Code 的做法是仅在左下角状态栏反映当前模式，不在历史区打印。
2. **状态栏 mode 文案无配色区分、无切换提示**：四档模式文案颜色相同，用户无法一眼区分当前处于哪一档；且没有任何地方提示「用 Shift+Tab 切换」，导致用户不知道如何切换模式。
3. **切换后输入框失焦**：Shift+Tab 切换后，输入框失去焦点，光标消失，用户必须手动重新点击输入框才能继续输入，打断输入流。

## 目标

- Shift+Tab 切换 mode 时不再向对话历史区打印任何消息，仅在左下角状态栏反映当前模式。
- 状态栏 mode 文案按四档配色区分，并在非 DEFAULT 模式下显示 `(shift+tab to cycle)` 灰色提示，指明切换操作。
- Shift+Tab 切换后输入框保持聚焦，已输入文字不被清空。

## 功能需求

- F1: Shift+Tab 在 IDLE 态切换 mode 时，不向对话历史区追加任何消息。
- F2: 状态栏左侧 mode 文案按当前模式配色：DEFAULT 为默认前景色（不染色），ACCEPT_EDITS 为琥珀黄，PLAN 为青蓝，BYPASS 为红。状态栏前置的圆点 `●` 与 mode 文案同色。
- F3: 当 mode 非 DEFAULT 时，状态栏在 mode 文案之后、紧邻显示 `(shift+tab to cycle)` 提示文字，颜色为灰色 dim（与 mode 配色不同）；当 mode 为 DEFAULT 时不显示该提示。
- F4: 输入框边框副标题移除「Shift+Tab 切换模式」字样，仅保留发送相关提示（换行 / 发送）。
- F5: Shift+Tab 切换 mode 后，输入框保持聚焦状态（光标可见、可继续输入），且切换前已输入的文字不被清空。

## 非功能需求

- N1: 四档配色在终端默认深色主题下彼此清晰可辨，且与状态栏其余文字（model 名、token 用量）有足够区分度。
- N2: 改动限于 TUI 呈现与交互层，不影响权限引擎、mode 对工具/权限的实际生效逻辑，不影响 agent loop 行为。
- N3: 保持现有 ruff lint、mypy 类型检查与测试通过。

## 不做的事

- 不改变 mode 的循环切换顺序（DEFAULT -> ACCEPT_EDITS -> PLAN -> BYPASS -> DEFAULT）。
- 不改变 Shift+Tab 仅在 IDLE 态生效的约束（STREAMING / APPROVING / SELECTING 态不切换）。
- 不改动 `/plan`、`/do` 命令的现有行为与各自的历史区反馈。
- 不引入新的权限模式或新的权限规则。
- 不改变 mode 对工具/权限的实际生效逻辑（仅改 UI 呈现与输入焦点交互）。

## 验收标准

- AC1: 在 IDLE 态按 Shift+Tab 循环切换模式时，对话历史区不出现「已切换到 xxx 模式」之类消息。
- AC2: 状态栏 mode 文案颜色随模式变化——ACCEPT_EDITS 为琥珀黄、PLAN 为青蓝、BYPASS 为红、DEFAULT 为默认前景色；前置 `●` 圆点与文案同色。
- AC3: 处于 ACCEPT_EDITS / PLAN / BYPASS 任一模式时，状态栏 mode 文案后紧邻显示灰色 `(shift+tab to cycle)`；切换回 DEFAULT 时该提示消失。
- AC4: 输入框边框副标题不再包含「Shift+Tab」字样，仍保留「Alt+Enter 换行 · Enter 发送」类发送提示。
- AC5: 在输入框输入部分文字后按 Shift+Tab 切换模式，切换后输入框仍处于聚焦状态（光标可见），且已输入文字保留在输入框内。
- AC6: Shift+Tab 在 STREAMING / APPROVING / SELECTING 态按下时不触发 mode 切换（保持原有行为不变）。
