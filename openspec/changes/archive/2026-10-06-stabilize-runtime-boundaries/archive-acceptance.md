# 归档验收记录

日期：2026-10-06；提交版本：0.1.24。

本次依次同步并归档 `stabilize-runtime-boundaries`（63/63 任务）和 `simplify-and-harden-teaching-runtime`（39/39 任务），两者均使用 spec-driven schema，所有规划产物完成。归档验收针对后续 change 完成后的当前源码；较早 change 的原实现验收记录保留为历史证据，不要求重新引入已由后续 change 删除的结构。

## 规格衔接

先同步较早 change 的 4 个能力规格，再同步后续 change。后续 change 在 runtime-boundaries delta 中显式移除 Turn Engine、Session Controller、旧兼容门面和阶段性迁移接缝的 4 条要求；保留其余既有要求及场景，并将两个 change 的 Team 要求合并到同一主规格。最终主规格覆盖 9 个能力。

## 当前自动化验证

- WSL/Linux：`uv run --locked python scripts/validate_change.py` 通过，完整 pytest 为 725 passed，架构测试为 5 passed，Ruff lint 和格式检查通过。
- 增量门禁覆盖 102 个文件：mypy baseline=180、current=180、new=0；C901 baseline=21、current=21、new=0。既有类型和复杂度诊断仍存在，本次没有新增诊断。
- 补丁版本从 0.1.23 递增至 0.1.24；`uv lock --check`、`uv sync --locked`、配置与版本测试 22 passed、CLI 版本及帮助检查通过。pyproject、运行时、锁文件和安装元数据一致，锁文件仅修改 NovaCode 版本。
- 两个 change 归档前通过 OpenSpec 严格校验；归档后复核主规格严格校验、归档任务完成度、源目录与目标目录、归档文件完整性及 `git diff --cached --check`。

## 当前 tmux 真实交互

使用隔离 HOME 和 Git 项目，在 tmux 中通过真实 CLI 0.1.24 与真实 deepseek-v4-flash Provider 发起 read_file 请求。主 Agent 读取 sample.txt 并返回包含 ARCHIVE_SAMPLE_OK 和 ARCHIVE_FINAL_OK 的回复；只读后台 Hook 也执行 read_file，结果通知可观察。

主回复包含 940 个 Provider 文本 chunk，合并为 166 次刷新，最终回复为 1,664 字符。完成时尚未刷新的尾部立即显示，回复与 Session 落盘内容一致；Memory Extraction 正常完成。退出后流式定时器、记忆 worker、Hook 任务及事件消费者无残留，Provider 关闭一次，CLI 正常退出，tmux 验收会话无残留。

后台 Hook 已完成，但其自然语言结果要求核对 Hook 提示中的期望标记与文件标记，未输出预设成功短语；本次只据此确认只读工具调用、完成通知及资源收束。该结果不扩大为新的 Hook 语义验收结论。

## 验证边界

本轮没有重新运行原生 Windows 全量测试；两次功能变更原有的 Windows 验收记录保持为历史证据。归档与版本更新不改变运行逻辑，远端双平台 CI 结果需以推送后的实际运行状态为准。

README.md 的既有修改按原始字节保留并排除本次提交；docs/ 未读取或修改。验收配置、密钥及运行日志留在被忽略的隔离目录，不进入提交。
