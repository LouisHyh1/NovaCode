## ADDED Requirements

### Requirement: Agent Run and Team Task are distinct concepts
Internal domain APIs MUST use Agent Run for an Agent execution and Team Task for a durable Team work unit. An unqualified `Task` MUST NOT be introduced in new runtime or Team domain interfaces.

#### Scenario: New runtime API review
- **WHEN** the runtime boundary exports its public domain types
- **THEN** execution types use Agent Run terminology and Team collaboration types use Team Task terminology

### Requirement: Turn Engine exposes a typed asynchronous event stream
The runtime core MUST define a Turn Engine contract that accepts a typed turn request and asynchronously yields typed text, tool, approval, usage, completion, cancellation, and error events. Provider access, tool execution, permission decisions, hooks, and persistence MUST enter through injected Port protocols.

#### Scenario: Legacy Agent adapter
- **WHEN** the current Agent loop is invoked through the initial Turn Engine adapter
- **THEN** callers consume the typed event contract without requiring the complete Agent implementation to migrate in this change

#### Scenario: Cancellation event
- **WHEN** a turn is cancelled through the Turn Engine contract
- **THEN** the event stream terminates with a typed cancellation outcome and does not report successful completion

### Requirement: Session Controller owns one explicit session lifecycle
The application layer MUST define a Session Controller object for one active session with explicit `start`, `submit`, `switch_session`, `cancel`, and `close` lifecycle operations. It MUST own session-scoped Turn Engine, writer, approval, and cancellation resources and receive an immutable dependency bundle at construction.

#### Scenario: Atomic session switch
- **WHEN** a Session Controller switches from one valid session to another
- **THEN** the candidate session is prepared before publication, the old resources are closed after a successful swap, and failure leaves the old session active

#### Scenario: Idempotent close
- **WHEN** `close` is called more than once
- **THEN** resources are released at most once and every call returns a consistent operation report

### Requirement: Dependency direction is enforced
Runtime and domain modules MUST depend only on domain types and Port protocols. Textual, Provider SDK implementations, filesystem repositories, tmux, and CLI composition MUST be adapters that depend inward. Automated import-boundary tests MUST reject a prohibited dependency.

#### Scenario: Core imports Textual
- **WHEN** a runtime or domain module imports a Textual module
- **THEN** the architecture test fails and identifies the prohibited import edge

### Requirement: Typed errors cross architectural boundaries
Core and repository operations MUST use explicit error types for conflict, validation, corruption, migration, cancellation, timeout, and cleanup failure. Adapters MUST translate them into logs, user messages, or operation reports without losing the error category.

#### Scenario: Corruption error reaches TUI adapter
- **WHEN** a repository reports persisted-state corruption
- **THEN** the adapter displays a non-success diagnostic with the error category and affected path, while the core retains the typed error

### Requirement: Compatibility facades remain during migration
The existing `novacode.task` imports and `TaskList`/`TaskGet` tool names MUST remain usable for at least one released version after the new boundaries are introduced. Compatibility code MUST delegate to the new semantics and MUST NOT create a second source of state.

#### Scenario: Existing TaskList caller
- **WHEN** an existing caller invokes `TaskList` without Team context
- **THEN** it receives Agent Run information through the compatibility facade

#### Scenario: Team-scoped TaskList caller
- **WHEN** a Team-scoped caller invokes `TaskList`
- **THEN** it receives Team Task information from the Team Task Repository

### Requirement: This change creates seams without full UI migration
This change MUST introduce contract implementations, compatibility adapters, and executable architecture tests, but MUST NOT require complete migration of `Agent.run` or `NovaCodeApp` behavior.

#### Scenario: Change completion
- **WHEN** all tasks in this change are accepted
- **THEN** existing user-visible Agent and TUI behavior remains compatible while the new Turn Engine and Session Controller seams are available for a later migration
