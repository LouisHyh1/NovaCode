"""Git Worktree 隔离。"""

from novacode.worktree.lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from novacode.worktree.manager import Manager, Worktree
from novacode.worktree.session import WorktreeSession
from novacode.worktree.slug import flat_slug, validate_slug
from novacode.worktree.sweep import random_agent_name

__all__ = [
    "AutoCleanupReport",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "Manager",
    "Worktree",
    "WorktreeHasChangesError",
    "WorktreeSession",
    "flat_slug",
    "random_agent_name",
    "validate_slug",
]
