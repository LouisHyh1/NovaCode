"""跨运行时边界传播的稳定错误类型。"""

from __future__ import annotations


class NovaCodeError(RuntimeError):
    category = "runtime"


class ConflictError(NovaCodeError):
    category = "conflict"


class ValidationError(NovaCodeError):
    category = "validation"


class DependencyCycleError(ValidationError):
    category = "dependency_cycle"


class StateCorruptionError(NovaCodeError):
    category = "state_corruption"

    def __init__(self, path: str, *, detail: str = "") -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"持久化状态损坏，需要人工恢复: {path}")


class UnsupportedSchemaError(StateCorruptionError):
    category = "unsupported_schema"


class MigrationError(NovaCodeError):
    category = "migration"


class OperationCancelled(NovaCodeError):  # noqa: N818 - OpenSpec 约定的公开类型名
    category = "cancellation"


class OperationTimeout(NovaCodeError):  # noqa: N818 - OpenSpec 约定的公开类型名
    category = "timeout"


class CleanupError(NovaCodeError):
    category = "cleanup"

    def __init__(self, message: str, *, residual_path: str = "") -> None:
        self.residual_path = residual_path
        super().__init__(message)
