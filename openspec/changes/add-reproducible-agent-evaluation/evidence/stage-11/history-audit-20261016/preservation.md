# 历史现场只读保存核验

2026-10-16 实施从 11.7 开始。新输出全部位于本目录；旧模型队列未启动，正式模型调用为零。

`evaluation/formal/preserve_history.py` 对历史 v1/v2、原冻结资产和既存 stage-11 证据递归记录普通文件 SHA-256、符号链接目标、类型和模式，不跟随链接。清单为 [preservation.json.gz](preservation.json.gz)，共 90,183 个主机条目；包含第 147 次保留业务工作区及其 Git 对象，不再沿用此前未覆盖该工作区的静态检查口径。

使用当前用户可访问的 Docker Unix socket 执行 GET 只读请求，确认保留容器退出状态、ExitCode=255、OOMKilled=false；读取其 `/testbed` tar 流逐成员计算内容、类型与模式，共 82,745 个条目，未启动或修改容器。另记录容器 ID、镜像身份、状态和 changes 列表，不输出配置或凭证。WSL 的 Docker CLI 位于 `/snap/bin/docker`，普通 PATH 未包含它，守护进程没有权限阻塞。

147 个已开始原计划终态与 147 份归档一一对应；前后两次递归指纹和容器状态完全一致，见 [preservation-verified.json](preservation-verified.json)。[external-before.json](external-before.json) 补充核对旧 manifest 的 95 个外部原执行资产，包括运行时、wheel、原数据和固定判题源码，全部与原哈希一致。后续恢复输出不写入任何原目录。

核验命令为 `.venv/bin/python evaluation/formal/preserve_history.py --output openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/history-audit-20261016`，随后加 `--verify` 作第二遍比对。清单独占创建；已存在的验证收据不会覆盖。此结果仅完成保全前置，不代表历史结果已完整恢复或独立复判。
