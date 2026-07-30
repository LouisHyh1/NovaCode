# 持久 Team 状态与可替换 Worker Host

NovaCode 将 Team、Member、Task、Mailbox 和 Member Session 作为磁盘上的长期权威状态，把 tmux pane、协程 Worker Host 和每次 Member Execution 视为可替换运行租约。我们没有序列化现有内存 `BackgroundTask`，因为进程对象无法提供跨重启一致性，而且会把一次执行的失败错误升级为成员或任务消失；代价是需要 Host Lease、fencing token、事件日志重放和显式恢复状态机。
