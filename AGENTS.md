# KoyoCode

我正在构建一个终端 AI 编程助手（类似 Claude Code），项目名叫 KoyoCode，使用 Python 实现。

## 语言

中文回答，中文注释。

## 测试

开发完功能后，用 Windows Terminal + 新 PowerShell 窗口 做端到端测试：

1. 在 终端 中启动 KoyoCode
2. 输入一段真实的对话请求
3. 观察 KoyoCode 是否正确调用工具、生成回复
4. 对照 checklist.md 逐项验收

## 分支命名

开发分支统一使用 `<类型>/<简述>` 形式，简述用英文小写连字符。功能/特性开发分支用 `feat/` 前缀，例如 `feat/agent-loop`、`feat/system-prompt`；其他类型如 `fix/`（修复）、`docs/`（文档）、`chore/`（构建/杂项）按改动性质选用。
