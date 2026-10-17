"""多资源操作的结构化结果与聚合规则。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResourceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class OperationStatus(StrEnum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ResourceResult:
    resource: str
    status: ResourceStatus
    error_category: str = ""
    message: str = ""
    residual_path: str = ""


@dataclass(frozen=True, slots=True)
class OperationReport:
    operation: str
    results: tuple[ResourceResult, ...] = ()

    @property
    def status(self) -> OperationStatus:
        failed = sum(result.status is ResourceStatus.FAILED for result in self.results)
        if failed == 0:
            return OperationStatus.COMPLETED
        succeeded = any(result.status is ResourceStatus.SUCCEEDED for result in self.results)
        if succeeded:
            return OperationStatus.INCOMPLETE
        return OperationStatus.FAILED

    @property
    def residual_paths(self) -> tuple[str, ...]:
        return tuple(result.residual_path for result in self.results if result.residual_path)
