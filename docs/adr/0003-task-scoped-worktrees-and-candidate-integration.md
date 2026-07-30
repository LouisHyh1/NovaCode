# Task 级 Worktree 与 Candidate 集成

每个代码 Team Task 使用独立 Worktree/branch，Lead 把 Delivery 合入从当前 Integration HEAD 派生的可丢弃 Candidate，只有合并和 Verification Profile 全部成功才 fast-forward 已接受分支。相比长期 Member branch 或直接在目标分支 merge/reset，这一结构让 Task、提交、验证和回滚一一对应，并确保失败从未污染 Integration/目标分支；代价是增加 Worktree 数量和渐进集成管理成本。
