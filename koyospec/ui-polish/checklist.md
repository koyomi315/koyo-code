# UI Polish Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。所有端到端项在真实终端（Windows Terminal / 新 PowerShell 窗口）启动 KoyoCode 后执行。

## 实现完整性

- [ ] banner 点阵 logo 可渲染：`uv run python -c "from koyocode.prompt import render_banner; t=render_banner('0.1.0','/tmp'); print(type(t).__name__); print(len(t.spans))"` 输出 `Text` 且 spans 数 > 0（验证：logo 用 rich.Text 真彩色背景 span 拼出）
- [ ] logo 像素着色为鲸鱼蓝：检查 `render_banner` 返回的 Text 中存在 `bgcolor` 为 `#2496ED` 的 span（验证：用 Python 取 spans 检查 style.bgcolor）
- [ ] `CAT_BANNER` 猫咪 ASCII 已从 `prompt/__init__.py` 移除（验证：`grep -n CAT_BANNER src/koyocode/prompt/__init__.py` 无输出）
- [ ] 用户消息符号为 `❯`、助手为 `●`（验证：单元测试断言用户/助手 widget 文本前缀）
- [ ] 工具参数折叠函数 `_fold_args` 存在且超 60 字符截断加 `…`（验证：单元测试）
- [ ] 状态栏完成态属性 `_done_feedback_until` 与 `_flash_done`/`_clear_done` 已实现（验证：`grep -n "_flash_done\|_done_feedback_until" src/koyocode/tui/app.py` 有定义）
- [ ] 旋转指示符常量 `_SPINNER_FRAMES` 已定义（验证：grep 命中）

## 集成

- [ ] `on_mount` 通过 `_append_history_rich` 挂载 banner（验证：单元测试 mock 启动，断言调用路径与 `banner-text` 类）
- [ ] `_finish_turn` 调用 `_flash_done(elapsed)`（验证：grep 命中调用点）
- [ ] `_finish_turn` 中有二次 `scroll_end` 调用（验证：grep / 单元测试断言滚动到底）
- [ ] `_start_turn` 非首轮追加回合分隔（验证：单元测试 `_turn_count` 逻辑）
- [ ] banner 返回类型改为 `Text` 后，唯一调用方 `on_mount` 同步适配，无 `str` 残留调用（验证：grep `render_banner` 调用点类型一致）
- [ ] 完成提示不破坏常规状态栏：`_clear_done` 后恢复模式/model/token（验证：单元测试 timer 回调后输出含 model）
- [ ] 错误态 `_finish_with_error` 不触发完成提示（验证：grep 确认无 `_flash_done` 调用）

## 编译与测试

- [ ] 项目编译无错误（验证：`uv run python -c "import koyocode.tui.app"` 无异常）
- [ ] 所有单元测试通过（验证：`uv run pytest -q` 全绿）
- [ ] ruff 检查通过（验证：`uv run ruff check .` 无告警）
- [ ] mypy 类型检查通过（验证：`uv run mypy src/koyocode` 无错误）
- [ ] 无遗留 TODO / 占位符（验证：`grep -rn "TODO\|TBD\|FIXME" src/koyocode/tui/app.py src/koyocode/prompt/__init__.py`）

## 端到端场景

- [ ] 场景 1（AC1 banner）：启动 KoyoCode -> 顶部显示鲸鱼蓝方块拼出的 `KOYOCODE` logo + 下方应用名/版本、cwd、按键提示，无猫咪 ASCII（验证：肉眼观察）
- [ ] 场景 2（AC2 自动滚动）：发送一条会产生较长回复的请求 -> 生成完成后历史区自动滚到底部，最新 AI 回复完整可见，无需手动滚动（验证：肉眼观察，对照改造前需手动滚的情况）
- [ ] 场景 3（AC3 完成提示）：一轮正常生成完成 -> 状态栏闪现绿色「✓ 完成 · Xs」约 2 秒 -> 自动恢复为「模式 · model · token」常规信息（验证：肉眼观察时序）
- [ ] 场景 4（AC4 用户/助手界限）：进行多轮对话后滚动回看 -> 用户输入行以 `❯` 标记、AI 回复行以青色 `●` 标记，可一眼区分并能快速定位每条 query 及其回复（验证：肉眼观察）
- [ ] 场景 5（AC5 配色符号统一）：历史区用户/助手/工具/结果/错误/耗时各角色符号与配色一致，错误信息醒目红色可辨（验证：肉眼观察触发一个工具调用与一个错误）
- [ ] 场景 6（AC6 流式动态区）：生成中动态区显示旋转字符（`⠋⠙⠹...`）+ 简洁计时，无 `Imagining...`/`Running...` 冗余文字（验证：肉眼观察）
- [ ] 场景 7（AC7 工具折叠）：触发一个长参数工具调用（如读取长路径文件）-> 工具行参数被折叠截断加 `…`，结果摘要 `└` 缩进截断清晰（验证：肉眼观察）
- [ ] 场景 8（AC8 回合分隔）：连续进行两轮以上对话 -> 相邻轮次间有暗淡细线分隔，每轮「query + 回复」成组、结构清晰（验证：肉眼观察）
- [ ] 场景 9（边界：错误不闪现完成）：触发一个会报错的请求（如无权限工具）-> 错误信息红色显示，状态栏不出现「✓ 完成」（验证：肉眼观察）
- [ ] 场景 10（回归：审批/选区不破坏）：生成中触发权限审批菜单正常显示与操作；历史区文本仍可拖选复制（验证：肉眼观察交互未被破坏）
