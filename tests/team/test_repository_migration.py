from __future__ import annotations

import json
from pathlib import Path

import pytest

from novacode.runtime.errors import StateCorruptionError, UnsupportedSchemaError
from novacode.team.domain import TeamId, TeamState
from novacode.team.repository import JsonTeamRepository
from novacode.team.task_repository import JsonTeamTaskRepository


@pytest.fixture
def restore_migration_backup():
    def restore(path: Path) -> bytes:
        backup = path.with_name(f"{path.name}.migration-v0.bak")
        evidence = backup.read_bytes()
        path.write_bytes(evidence)
        return evidence

    return restore


def _legacy_team() -> dict:
    return {
        "name": "demo",
        "sanitized_name": "demo",
        "lead_agent_id": "lead",
        "backend": "in-process",
        "description": "legacy",
        "created_at": 123.5,
        "permission_mode": "default",
        "members": [
            {
                "name": "lead",
                "agent_id": "lead",
                "backend_type": "in-process",
                "is_active": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_legacy_team_is_backed_up_and_atomically_migrated(isolated_state) -> None:
    path = isolated_state.team / "config.json"
    original = (json.dumps(_legacy_team(), ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(original)
    repository = JsonTeamRepository(path)

    migrated = await repository.load_existing()
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert path.with_name("config.json.migration-v0.bak").read_bytes() == original
    assert migrated.team_id
    assert persisted["schema_version"] == 1
    assert persisted["team_id"] == migrated.team_id
    assert persisted["sanitized_name"] == "demo"
    assert persisted["backend"] == "in-process"


@pytest.mark.asyncio
async def test_legacy_task_graph_migrates_with_team_id_and_compatibility_fields(
    isolated_state,
) -> None:
    original = {
        "tasks": [
            {
                "id": "task-1",
                "title": "legacy",
                "status": "pending",
                "assignee": "",
                "blocked_by": [],
                "blocks": [],
                "created_at": 1,
                "updated_at": 1,
            }
        ]
    }
    path = isolated_state.task_graph
    encoded = (json.dumps(original, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(encoded)
    repository = JsonTeamTaskRepository(path)

    graph = await repository.load(TeamId("team-1"))
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert path.with_name("tasks.json.migration-v0.bak").read_bytes() == encoded
    assert graph.team_id == TeamId("team-1")
    assert persisted["schema_version"] == 1
    assert persisted["tasks"][0]["id"] == "task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        (b"{secret-body", StateCorruptionError),
        (
            json.dumps({"schema_version": 1, "revision": 1, "name": "broken"}).encode(),
            StateCorruptionError,
        ),
        (
            json.dumps({"schema_version": 999, "revision": 1, "team_id": "x"}).encode(),
            UnsupportedSchemaError,
        ),
    ],
)
async def test_invalid_state_enters_recovery_required_without_leaking_body(
    isolated_state, raw, error_type
) -> None:
    path = isolated_state.team / "config.json"
    path.write_bytes(raw)
    repository = JsonTeamRepository(path)

    with pytest.raises(error_type) as captured:
        await repository.load_existing()
    diagnostic = repository.diagnostic()
    with pytest.raises(error_type):
        await repository.create(TeamState(TeamId("replacement"), "replacement"))

    assert diagnostic is not None
    assert diagnostic[1] == str(path)
    assert "secret-body" not in str(captured.value)
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_failed_migration_preserves_original_backup_and_recovery_evidence(
    isolated_state,
) -> None:
    path = isolated_state.team / "config.json"
    original = (json.dumps(_legacy_team(), ensure_ascii=False) + "\n").encode()
    path.write_bytes(original)

    def fail_before_publish(phase, _path) -> None:
        if phase == "before_publish":
            raise RuntimeError("migration publication failed")

    repository = JsonTeamRepository(path, fault_hook=fail_before_publish)
    with pytest.raises(RuntimeError, match="migration publication failed"):
        await repository.load_existing()

    assert path.read_bytes() == original
    assert path.with_name("config.json.migration-v0.bak").read_bytes() == original


@pytest.mark.asyncio
async def test_migration_backup_can_restore_and_remigrate_stable_team_id(
    isolated_state, restore_migration_backup
) -> None:
    path = isolated_state.team / "config.json"
    path.write_text(json.dumps(_legacy_team()), encoding="utf-8")
    first = await JsonTeamRepository(path).load_existing()

    evidence = restore_migration_backup(path)
    second = await JsonTeamRepository(path).load_existing()

    assert second.team_id == first.team_id
    assert path.with_name("config.json.migration-v0.bak").read_bytes() == evidence
