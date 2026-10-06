## ADDED Requirements

### Requirement: Search execution has finite resource budgets
The system MUST implement `glob` and `grep` through a shared Search Service with finite limits for returned results, scanned files, bytes per file, total scanned bytes, and elapsed time. Initial defaults MUST be 100 results, 20,000 files, 2 MiB per file, 64 MiB total input, and 10 seconds. Configuration MAY lower or raise these values within implementation-defined safety caps, but a tool call MUST NOT disable all limits.

#### Scenario: No-match search in a large tree
- **WHEN** a search has no matches and reaches its file, byte, or time budget
- **THEN** scanning stops within the configured budget and returns a truncated result instead of traversing the remaining tree

#### Scenario: Result limit reached
- **WHEN** a search finds 100 results under the default policy
- **THEN** it stops collecting additional results and reports that the result limit caused truncation

### Requirement: Search does not block the event loop
Filesystem traversal and content scanning MUST run outside the event-loop thread. Cancellation MUST set a cooperative stop signal that the scanner checks between filesystem entries and file chunks.

#### Scenario: Event-loop responsiveness during search
- **WHEN** a search scans a large fixture while another coroutine emits progress ticks
- **THEN** progress ticks continue before the search completes

#### Scenario: Search cancellation
- **WHEN** the caller cancels an active search
- **THEN** the Search Service signals the worker to stop, returns or raises the typed cancellation outcome, and does not continue unbounded background scanning

### Requirement: Ignore policy is shared and configurable
The Search Service MUST ignore `.git`, Python virtual environments, `node_modules`, cache directories, and NovaCode-managed Worktrees by default. Project configuration MUST be able to add ignore patterns without disabling sensitive-file filtering.

#### Scenario: Default ignored directories
- **WHEN** ignored and non-ignored directories contain matching files
- **THEN** default searches return only matches from non-ignored directories

### Requirement: Budget truncation is explicit and structured
Search results MUST contain hits plus metadata for `truncated`, truncation reason, scanned file count, scanned byte count, and elapsed time. Tool adapters MUST preserve the existing human-readable content while exposing the structured metadata to callers.

#### Scenario: Partial matches before timeout
- **WHEN** a search finds matches and subsequently reaches its time budget
- **THEN** the found matches are retained, `truncated` is true, and the metadata identifies the time budget as the reason

### Requirement: File reads are bounded before allocation
The read-file path MUST enforce line, character, and byte limits while reading rather than loading an unbounded file and truncating afterward. The default visible limits MUST remain compatible with 2,000 lines and 256 KiB of returned text.

#### Scenario: Oversized file read
- **WHEN** a file is larger than the configured read budget
- **THEN** the tool reads only the bounded prefix, returns it with explicit truncation metadata, and does not allocate memory proportional to the complete file

### Requirement: Sensitive files remain excluded
Search and file-read refactoring MUST preserve the existing permission-layer and tool-layer sensitive-file defenses. Ignored-directory configuration or budget metadata MUST NOT reveal sensitive paths.

#### Scenario: Sensitive selector under searchable root
- **WHEN** a glob or grep pattern could match a protected credential file
- **THEN** the protected path and its content are absent from hits, diagnostics, and truncation metadata
