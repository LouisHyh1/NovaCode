# team-collaboration-state

## Purpose
Maintain Team identity, transactional collaboration state, strict task dependencies, and auditable recovery.

## Requirements

### Requirement: Team-scoped agent identity
The system MUST assign every Team a stable globally unique Team ID and every Agent a globally unique Agent ID. A Member Name MUST be unique only within its Team, and Team member lookup MUST use an Agent Address composed of Team ID and Member Name. Agent Run names MUST use a registry separate from Team membership.

#### Scenario: Same member name in different Teams
- **WHEN** two Teams each register a member named `alice`
- **THEN** both registrations succeed and messages addressed to either Team reach only that Team's `alice`

#### Scenario: Duplicate member name inside one Team
- **WHEN** a Team already contains a member named `alice` and another member with that name is added
- **THEN** the operation fails without changing the Team or any Agent Run registration

### Requirement: Transactional aggregate repositories
The system MUST expose asynchronous repositories for the Team, Team Task Graph, and Mailbox aggregates. Each repository MUST provide cross-process mutual exclusion, unique transaction temporary files, atomic publication, ownership-safe lock release, and failure atomicity. Blocking filesystem operations MUST execute outside the event-loop thread.

#### Scenario: Concurrent Team member updates
- **WHEN** two processes update different members of the same Team concurrently
- **THEN** both committed updates are present and neither process can delete or steal the other process's live lock

#### Scenario: Failure before publication
- **WHEN** a Team repository write fails after preparing a candidate but before atomic publication
- **THEN** readers observe the complete previous state and no partial candidate becomes authoritative

#### Scenario: Concurrent mailbox writers
- **WHEN** multiple processes append messages to the same mailbox
- **THEN** every successful append remains readable exactly once after the writers finish

### Requirement: Team Task dependency graph is a strict DAG
The Team Task Repository MUST validate the complete candidate graph before a write. Every referenced dependency MUST exist, self-dependencies and cycles MUST be rejected, and the task plus both directions of every dependency edge MUST commit in one transaction.

#### Scenario: Missing blocker on task creation
- **WHEN** a new Team Task references a blocker ID that does not exist
- **THEN** creation fails and neither the new task nor any dependency edge is written

#### Scenario: Cycle introduced by update
- **WHEN** a Team Task update would introduce a direct or indirect dependency cycle
- **THEN** the update fails and the previously committed graph remains byte-for-byte authoritative

#### Scenario: Valid dependency update
- **WHEN** an update adds an edge that preserves a valid DAG
- **THEN** `blocked_by` and `blocks` are committed consistently and readiness is derived from the committed graph

### Requirement: Member removal preserves task ownership integrity
Normal member removal MUST fail while that member owns an incomplete Team Task. Forced removal MUST unassign incomplete tasks before removing the member, while completed tasks MUST retain a historical assignee snapshot rather than an active member reference.

#### Scenario: Normal removal of an assigned member
- **WHEN** a member owns at least one incomplete Team Task and removal is not forced
- **THEN** removal fails and reports the blocking Team Task IDs

#### Scenario: Forced removal of an assigned member
- **WHEN** forced removal is requested for a member with incomplete and completed Team Tasks
- **THEN** incomplete tasks become unassigned, completed tasks preserve historical attribution, and no active task reference points to the removed member

### Requirement: Versioned migration and corruption protection
Team and Team Task state MUST include a schema version. A legacy file MUST be fully validated and backed up before an atomic forward migration. Unparseable, invalid, or unsupported state MUST enter a recovery-required condition that permits diagnostics but blocks writes; the system MUST NOT overwrite it from an in-memory snapshot.

#### Scenario: Successful legacy migration
- **WHEN** a valid legacy Team file is opened by the new repository
- **THEN** the repository preserves a backup and atomically publishes an equivalent versioned state with a stable Team ID

#### Scenario: Invalid persisted state
- **WHEN** a Team or Team Task file fails schema or integrity validation
- **THEN** the repository reports recovery-required with the affected path, preserves all evidence, and rejects subsequent writes

### Requirement: Multi-resource operations are auditable
Team deletion, forced member removal, and resource cleanup MUST return an operation report containing every attempted resource, outcome, error type, and residual path. The implementation MUST NOT silently suppress an unexpected cleanup or persistence failure.

#### Scenario: Partial cleanup failure
- **WHEN** Team deletion removes a session but fails to remove a Worktree
- **THEN** cleanup continues for independent safe resources and the final report identifies the residual Worktree and marks the overall operation incomplete

### Requirement: Team 仅支持进程内和 tmux 后端
系统 SHALL 只提供进程内与 tmux Team 后端，并 SHALL 删除 iTerm2 后端、自动检测分支和相关枚举值。

#### Scenario: 自动选择 Team 后端
- **WHEN** NovaCode 在支持 tmux 的环境选择跨进程 Team
- **THEN** 系统选择 tmux；否则使用进程内后端，且不存在 iTerm2 选择结果

#### Scenario: 请求已删除的 iTerm2 后端
- **WHEN** 旧配置请求 iTerm2 Team 后端
- **THEN** 配置校验明确报告不支持，而不是静默选择未测试实现

### Requirement: Repository 是跨进程状态唯一事实来源
每个依赖 Team、成员或 Team Task 状态的操作 SHALL 从 Repository 读取最新快照并在该操作内使用，且 SHALL NOT 依赖长生命周期可变 Team 或 lead 缓存。

#### Scenario: tmux 成员更新 Repository
- **WHEN** 外部 tmux 成员在两个 lead 操作之间更新 Team Task 或成员状态
- **THEN** 后一个 lead 操作读取并使用更新后的 Repository 快照

#### Scenario: 写入 Team 状态
- **WHEN** 一个 Team 操作修改成员或 Team Task
- **THEN** 修改通过 Repository 原子更新且后续操作可立即观察

### Requirement: Team 成员继承父 Provider 配置
进程内 Team 成员 SHALL 借用父 Provider 实例；tmux 子进程 SHALL 从持久化启动信息恢复等价的父 Provider 配置，且 Team 派生参数 SHALL NOT 提供成员级 model 覆盖。

#### Scenario: 创建进程内 Team 成员
- **WHEN** lead 创建进程内成员
- **THEN** 成员使用 lead 的 Provider 实例且不创建新的连接池

#### Scenario: 创建 tmux Team 成员
- **WHEN** lead 启动 tmux 子进程成员
- **THEN** 子进程使用持久化的父 Provider 配置而不是配置列表中的任意第一个 Provider
