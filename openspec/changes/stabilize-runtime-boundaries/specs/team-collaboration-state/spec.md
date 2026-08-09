## ADDED Requirements

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
