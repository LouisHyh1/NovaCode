"""不修改进程 cwd 的工具工作目录上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ctx_cwd: ContextVar[str | None] = ContextVar("cwd", default=None)
_ctx_parent_cwd: ContextVar[str | None] = ContextVar("parent_cwd", default=None)


@contextmanager
def with_cwd(directory: str, *, parent_cwd: str | None = None):
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    parent_token = _ctx_parent_cwd.set(parent_cwd) if parent_cwd else None
    try:
        yield
    finally:
        if parent_token is not None:
            _ctx_parent_cwd.reset(parent_token)
        _ctx_cwd.reset(token)


def cwd_from_ctx() -> str | None:
    return _ctx_cwd.get()


def resolve_path(path: str) -> str:
    base = _ctx_cwd.get() or str(Path.cwd())
    if not path:
        return base
    candidate = Path(path)
    if candidate.is_absolute():
        parent_cwd = _ctx_parent_cwd.get()
        if parent_cwd:
            resolved = candidate.resolve()
            try:
                resolved.relative_to(Path(base).resolve())
            except ValueError:
                pass
            else:
                return str(candidate)
            try:
                relative = resolved.relative_to(Path(parent_cwd).resolve())
            except ValueError:
                pass
            else:
                return str(Path(base) / relative)
        return str(candidate)
    return str(Path(base) / candidate)
