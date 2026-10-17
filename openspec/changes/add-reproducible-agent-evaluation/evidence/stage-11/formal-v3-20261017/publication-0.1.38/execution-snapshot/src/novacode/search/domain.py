"""有界搜索的端口数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from novacode.runtime.errors import ValidationError

MAX_RESULTS_CAP = 10_000
MAX_FILES_CAP = 100_000
MAX_FILE_BYTES_CAP = 32 * 1024 * 1024
MAX_TOTAL_BYTES_CAP = 256 * 1024 * 1024
MAX_TIMEOUT_CAP = 60.0


@dataclass(frozen=True, slots=True)
class SearchBudget:
    max_results: int = 100
    max_files: int = 20_000
    max_file_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    timeout: float = 10.0

    def __post_init__(self) -> None:
        values = (
            self.max_results,
            self.max_files,
            self.max_file_bytes,
            self.max_total_bytes,
        )
        if any(value <= 0 for value in values) or self.timeout <= 0:
            raise ValidationError("搜索预算必须全部大于零")
        if self.max_results > MAX_RESULTS_CAP or self.max_files > MAX_FILES_CAP:
            raise ValidationError("搜索结果数或文件数超过安全上限")
        if self.max_file_bytes > MAX_FILE_BYTES_CAP:
            raise ValidationError("单文件搜索字节数超过安全上限")
        if self.max_total_bytes > MAX_TOTAL_BYTES_CAP:
            raise ValidationError("总搜索字节数超过安全上限")
        if self.timeout > MAX_TIMEOUT_CAP:
            raise ValidationError("搜索时限超过安全上限")


@dataclass(frozen=True, slots=True)
class ReadBudget:
    max_lines: int = 2_000
    max_chars: int = 256 * 1024
    max_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.max_lines <= 0 or self.max_chars <= 0 or self.max_bytes <= 0:
            raise ValidationError("文件读取预算必须全部大于零")
        if self.max_lines > 10_000 or self.max_chars > 4 * 1024 * 1024:
            raise ValidationError("文件读取可见预算超过安全上限")
        if self.max_bytes > 8 * 1024 * 1024:
            raise ValidationError("文件读取字节数超过安全上限")


class SearchKind(StrEnum):
    GLOB = "glob"
    GREP = "grep"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    kind: SearchKind
    root: str
    pattern: str
    file_glob: str = ""
    budget: SearchBudget | None = None
    additional_ignores: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadRequest:
    path: str
    budget: ReadBudget | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[str, ...]
    truncated: bool = False
    reason: str = ""
    scanned_files: int = 0
    scanned_bytes: int = 0
    elapsed: float = 0.0


@dataclass(frozen=True, slots=True)
class ReadResult:
    content: str
    truncated: bool = False
    reason: str = ""
    lines: int = 0
    bytes_read: int = 0
