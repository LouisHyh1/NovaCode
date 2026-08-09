from novacode.runtime.errors import (
    CleanupError,
    ConflictError,
    DependencyCycleError,
    MigrationError,
    OperationCancelled,
    OperationTimeout,
    StateCorruptionError,
    ValidationError,
)
from novacode.runtime.reports import (
    OperationReport,
    OperationStatus,
    ResourceResult,
    ResourceStatus,
)


def test_runtime_error_types_are_distinguishable_and_corruption_is_safe() -> None:
    errors = (
        ConflictError("conflict"),
        ValidationError("invalid"),
        DependencyCycleError("cycle"),
        MigrationError("migration"),
        OperationCancelled("cancelled"),
        OperationTimeout("timeout"),
        CleanupError("cleanup"),
    )
    assert len({error.category for error in errors}) == len(errors)

    corruption = StateCorruptionError("/state/team.json", detail="secret body")
    assert corruption.path == "/state/team.json"
    assert "secret body" not in str(corruption)


def test_operation_report_aggregates_resource_results() -> None:
    completed = OperationReport(
        "close",
        (
            ResourceResult("session", ResourceStatus.SUCCEEDED),
            ResourceResult("mailbox", ResourceStatus.SKIPPED),
        ),
    )
    incomplete = OperationReport(
        "delete-team",
        (
            ResourceResult("session", ResourceStatus.SUCCEEDED),
            ResourceResult(
                "worktree",
                ResourceStatus.FAILED,
                error_category="cleanup",
                residual_path="/repo/worktree",
            ),
        ),
    )
    failed = OperationReport(
        "delete-team",
        (ResourceResult("team", ResourceStatus.FAILED, error_category="conflict"),),
    )

    assert completed.status is OperationStatus.COMPLETED
    assert incomplete.status is OperationStatus.INCOMPLETE
    assert incomplete.residual_paths == ("/repo/worktree",)
    assert failed.status is OperationStatus.FAILED
