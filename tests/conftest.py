"""跨平台测试状态隔离 fixture。"""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class IsolatedState:
    root: Path
    home: Path
    project: Path
    team: Path
    task_graph: Path
    mailbox: Path
    search_root: Path


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedState:
    root = tmp_path / "state"
    home = root / "home"
    project = root / "project"
    team = root / "team"
    task_graph = team / "tasks.json"
    mailbox = team / "mailbox"
    search_root = root / "search"
    for directory in (home, project, team, mailbox, search_root):
        directory.mkdir(parents=True)
    monkeypatch.setenv("NOVACODE_TEST_STATE_ROOT", str(root))
    return IsolatedState(root, home, project, team, task_graph, mailbox, search_root)


@pytest.fixture
def spawn_context() -> multiprocessing.context.BaseContext:
    """Windows/Linux 均使用 spawn，避免 fork 隐式继承锁状态。"""
    return multiprocessing.get_context("spawn")
