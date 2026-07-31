# UI Polish Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。所有端到端项在真实终端（Windows Terminal / 新 PowerShell 窗口）启动 KoyoCode 后执行。

## 实现完整性

- [x] banner 点阵 logo 可渲染：运行命令输出 `Text 84`（证据：type=Text, spans=84>0）
- [x] logo 像素着色为鲸鱼蓝：Python 检查含 81 个 bgcolor=#2496ed 的像素 span（证据：span 数 81>0）
- [x] `CAT_BANNER` 已移除（证据：grep 无输出）
- [x] 用户 `❯`、助手 `●`（证据：test_user_message_uses_arrow_marker 通过 + 快照 ❯ 读文件并总结）
- [x] `_fold_args` 超 60 截断加 …（证据：test_fold_args_truncates_long 通过 + 快照工具行参数以 … 结尾）
- [x] 完成态属性与方法已实现（证据：grep 命中 _done_feedback_until/_flash_done/_clear_done 定义）
- [x] `_SPINNER_FRAMES` 已定义（证据：grep 命中）

## 集成

- [x] on_mount 通过 _append_history_rich 挂载 banner（证据：grep 命中 _append_history_rich(render_banner(...), banner-text)）
- [x] _finish_turn 调用 _flash_done(elapsed)（证据：grep 命中）
- [x] 滚动到底（实现改进为跟随定时器方案 _start_follow_scroll/_schedule_follow_scroll_stop，优于原二次 scroll_end；证据：test_history_auto_scrolls_to_bottom_after_turn 通过 + 用户终端确认效果 OK）
- [x] _start_turn 非首轮追加回合分隔（证据：test_turn_separator_between_rounds 通过 + 快照分隔线数=1）
- [x] render_banner 调用点类型一致（证据：唯一调用点走 _append_history_rich，无 str 残留）
- [x] _clear_done 后恢复常规状态栏（证据：test_finish_turn_flashes_done_in_statusbar 通过，_done_feedback_until 可清除）
- [x] 错误态不触发完成提示（证据：grep 确认 _finish_with_error 无 _flash_done + test_error_does_not_flash_done 通过）

## 编译与测试

- [x] 项目编译无错误（证据：import koyocode.tui.app 无异常）
- [x] 所有单元测试通过（证据：pytest 224 passed）
- [x] ruff 检查通过（证据：All checks passed!）
- [x] mypy 类型检查通过（证据：no issues found in 34 source files）
- [x] 无遗留 TODO/占位符（证据：grep 无输出）

## 端到端场景

- [x] 场景1（AC1 banner）：快照含 KoyoCode v0.1.0/cwd/按键提示、无猫咪；蓝色方块 logo 已由用户终端肉眼确认
- [x] 场景2（AC2 自动滚动）：test_history_auto_scrolls_to_bottom_after_turn 通过 + 用户终端确认效果 OK（跟随定时器方案）
- [x] 场景3（AC3 完成提示）：快照状态栏 ✓ 完成 · 0s、_done_feedback_until 已设；test_finish_turn_flashes_done_in_statusbar 通过
- [x] 场景4（AC4 用户/助手界限）：快照用户行 ❯ 读文件并总结、助手 Markdown；test_user_message_uses_arrow_marker 通过
- [x] 场景5（AC5 配色符号统一）：快照工具行 ● read_file(...)；CSS 各角色类样式一致（.user-message/.assistant-marker/.tool-line/.tool-error 等）
- [x] 场景6（AC6 流式动态区）：快照 ⠋ Xs、无 Imagining/Running；test_streaming_uses_spinner_no_imagining 通过
- [x] 场景7（AC7 工具折叠）：快照工具行长参数以省略号结尾；test_fold_args_truncates_long 通过
- [x] 场景8（AC8 回合分隔）：快照两轮后分隔线数=1；test_turn_separator_between_rounds 通过
- [x] 场景9（边界：错误不闪现完成）：test_error_does_not_flash_done 通过（_done_feedback_until 为 None、状态栏无 ✓ 完成）
- [x] 场景10（回归：审批/选区不破坏）：现有审批/选区测试全通过（test_approval_*、test_copy_selected_text_* 共 10+ 项未受改动影响）
