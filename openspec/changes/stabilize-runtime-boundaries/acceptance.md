# 验收记录

日期：2026-07-31

## 自动化验证

- WSL/Linux 全量回归：`674 passed`。
- 原生 Windows 全量回归：`671 passed, 3 skipped`；跳过项均为既有平台条件。
- WSL/Linux 事务仓储、多进程和故障注入：`25 passed`。
- 原生 Windows 事务仓储、多进程和故障注入：`25 passed`。
- WSL/Linux 与原生 Windows 均通过 `scripts/validate_change.py`，覆盖：
  - `uv lock --check`
  - Ruff lint
  - Ruff format check
  - 新边界目标 strict mypy
  - 架构测试
  - `--version` 与 `--help` 非交互 smoke
- CI 配置契约测试确认 `ubuntu-latest` 与 `windows-latest` 均包含上述门禁和全量 pytest。

## tmux 真实交互

在 tmux 会话 `novacode-accept-0731` 中启动 `uv run --locked nova`，选择
`deepseek-v4-pro`，完成以下真实对话：

1. 要求必须使用 `read_file` 读取 `pyproject.toml`。NovaCode 正确调用工具，并回答
   `name = "novacode"`、`version = "0.1.14"`。
2. 要求使用 `bash` 运行同名成员作用域路由和 Team Task DAG 原子性测试。审批选择
   “允许本次”，命令返回 `6 passed`；最终回复说明两个 Team 的 `alice` 分别解析到
   `agent-first`、`agent-second`，消息只进入选中 Team，并确认四类非法依赖候选均未改变
   权威文件字节。
3. 要求在 `/tmp/novacode-search-accept-0731` 创建 150 个 `.txt` fixture，再使用真实
   `glob` 工具搜索。界面保持响应，工具返回 100 条部分结果并显示
   `[truncated: result_limit]`；最终回复明确实际命中 150、返回 100、发生截断且
   `reason=result_limit`。

验收后使用 `Ctrl+C` 正常关闭 tmux 会话。

## 残留风险与后续边界

- 本变更按设计只提供 `LegacyAgentTurnEngine`、`SessionController` 和组合根接缝，未迁移
  完整 `Agent.run` 或 `NovaCodeApp`；后续变更应沿现有 Port 逐步迁移，不复制运行状态。
- `novacode.task` 旧命名保留一个发布周期并发出 `DeprecationWarning`；现有旧导入回归测试
  因而产生 16 条预期 warning，后续移除前需要独立兼容性变更。
- 未提交或推送，因此 GitHub Actions 没有远端运行记录；本地使用与 CI 相同的命令在
  WSL/Linux 和原生 Windows 均已通过。
- tmux 对话期间出现既有后台提示
  `memory governance failed: create=0 update=0 delete=0`。它未影响三项验收结果，也不在本次
  运行时边界变更范围内；后续应单独诊断治理任务为什么生成空动作后被报告为失败。
