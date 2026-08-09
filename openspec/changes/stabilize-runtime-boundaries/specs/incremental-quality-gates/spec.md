## ADDED Requirements

### Requirement: Static quality gates are incremental
CI MUST run Ruff lint, Ruff format check, and strict mypy for the newly introduced runtime, application, repository, and search boundary modules. Existing modules outside those boundaries MUST NOT block this change solely because of pre-existing strict-rule findings, while touched code MUST NOT add new complexity or broad-exception findings.

#### Scenario: Untyped new boundary API
- **WHEN** a new public function in a strict target module lacks a valid type contract
- **THEN** mypy fails the quality gate

#### Scenario: Pre-existing warning outside the change
- **WHEN** an untouched legacy module has a previously known strict-rule warning
- **THEN** the incremental gate does not require unrelated cleanup

### Requirement: Architecture boundaries are executable checks
The test suite MUST verify allowed package dependency direction and MUST fail on runtime/domain imports of Textual, concrete Provider SDKs, filesystem adapters, tmux, or CLI composition.

#### Scenario: Prohibited adapter dependency
- **WHEN** a core module adds a prohibited adapter import
- **THEN** the architecture test fails with the source and target modules

### Requirement: Persistence tests cover concurrency and faults
Repository acceptance MUST include real multi-process races and deterministic fault injection at lock acquisition, candidate write, atomic publication, migration, and cleanup boundaries.

#### Scenario: Two-process write race
- **WHEN** two spawned processes update one aggregate under a synchronization barrier
- **THEN** the test proves that no committed update is lost and no live lock is reclaimed

#### Scenario: Injected publication failure
- **WHEN** an injected failure occurs immediately before atomic publication
- **THEN** the test proves that the prior authoritative state remains readable

### Requirement: Full regression and startup checks remain mandatory
Local and CI acceptance MUST run the full pytest suite, locked dependency validation, Ruff lint, Ruff format check, strict target-module mypy, architecture tests, and a non-interactive NovaCode startup or version smoke check.

#### Scenario: Supported-platform CI
- **WHEN** the change is pushed to CI
- **THEN** all required checks pass on both `ubuntu-latest` and `windows-latest`

### Requirement: Real interactive acceptance uses tmux
Final implementation acceptance MUST start NovaCode in tmux, submit real dialogue requests that exercise Team consistency, bounded search, and unchanged normal conversation behavior, observe tool calls and final responses, and record the task-specific acceptance result.

#### Scenario: Bounded-search conversation
- **WHEN** a user asks NovaCode in tmux to search a large fixture with a deliberately small test budget
- **THEN** NovaCode calls the search tool, presents partial results with a truncation explanation, remains responsive, and completes the conversation

#### Scenario: Normal conversation regression
- **WHEN** a user submits a normal read-and-answer request in tmux
- **THEN** NovaCode performs the expected tool call and final response without exposing internal migration or boundary details
