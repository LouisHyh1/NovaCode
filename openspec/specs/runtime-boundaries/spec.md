# runtime-boundaries

## Purpose
Define Agent Run and Team Task terminology, inward dependencies, and typed architectural errors.

## Requirements

### Requirement: Agent Run and Team Task are distinct concepts
Internal domain APIs MUST use Agent Run for an Agent execution and Team Task for a durable Team work unit. An unqualified `Task` MUST NOT be introduced in new runtime or Team domain interfaces.

#### Scenario: New runtime API review
- **WHEN** the runtime boundary exports its public domain types
- **THEN** execution types use Agent Run terminology and Team collaboration types use Team Task terminology

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
