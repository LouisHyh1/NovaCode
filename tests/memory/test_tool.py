import json
import os

import pytest

from novacode.memory import ManageMemoryTool, MemoryKind, MemoryStore


def stores(tmp_path):
    user = MemoryStore(tmp_path / "user", frozenset({MemoryKind.USER, MemoryKind.FEEDBACK}))
    project = MemoryStore(
        tmp_path / "project",
        frozenset({MemoryKind.PROJECT, MemoryKind.REFERENCE}),
    )
    return user, project


def create_args(kind: str = "user") -> str:
    return json.dumps(
        {
            "action": "create",
            "kind": kind,
            "title": "Direction",
            "summary": "Agent development with Python",
            "content": "The user's current direction is Agent development and Python is used most.",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "target"),
    [("user", "user"), ("feedback", "user"), ("project", "project"), ("reference", "project")],
)
async def test_create_routes_to_fixed_store_and_refreshes_index(tmp_path, kind, target) -> None:
    user, project = stores(tmp_path)
    snapshots: list[str] = []
    tool = ManageMemoryTool(user, project, snapshots.append)

    result = await tool.execute(create_args(kind))

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert snapshots and "Direction" in snapshots[-1]
    selected = user if target == "user" else project
    other = project if target == "user" else user
    notes = [path for path in selected.directory.glob("*.md") if path.name != "MEMORY.md"]
    assert len(notes) == 1
    assert len(notes[0].stem) == 36
    assert not other.directory.exists() or not list(other.directory.glob("*.md"))


@pytest.mark.asyncio
async def test_update_and_delete_use_existing_id_without_exposing_filename(tmp_path) -> None:
    user, project = stores(tmp_path)
    tool = ManageMemoryTool(user, project, lambda _: None)
    created = await tool.execute(create_args())
    memory_id = json.loads(created.content)["memory_id"]

    updated = await tool.execute(
        json.dumps(
            {
                "action": "update",
                "kind": "user",
                "memory_id": memory_id,
                "summary": "Python is the primary language",
            }
        )
    )
    deleted = await tool.execute(
        json.dumps({"action": "delete", "kind": "user", "memory_id": memory_id})
    )

    assert updated.is_error is False and deleted.is_error is False
    assert not (user.directory / f"{memory_id}.md").exists()
    schema = tool.parameters()
    assert "filename" not in schema["properties"]
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"action": "create", "kind": "user"}),
        json.dumps(
            {"action": "create", "kind": "unknown", "title": "t", "summary": "s", "content": "c"}
        ),
        json.dumps(
            {
                "action": "create",
                "kind": "user",
                "title": "t",
                "summary": "s",
                "content": "c",
                "filename": "x.md",
            }
        ),
        json.dumps(
            {
                "action": "create",
                "kind": "user",
                "memory_id": "11111111-1111-4111-8111-111111111111",
                "title": "t",
                "summary": "s",
                "content": "c",
            }
        ),
        json.dumps({"action": "update", "kind": "user", "memory_id": "bad", "summary": "s"}),
        json.dumps(
            {
                "action": "delete",
                "kind": "user",
                "memory_id": "11111111-1111-4111-8111-111111111111",
            }
        ),
    ],
)
async def test_invalid_fields_and_ids_return_explicit_error(tmp_path, payload) -> None:
    user, project = stores(tmp_path)
    result = await ManageMemoryTool(user, project, lambda _: None).execute(payload)

    assert result.is_error is True
    assert "未写入" in result.content


@pytest.mark.asyncio
async def test_lock_and_transaction_failures_return_explicit_error(tmp_path, monkeypatch) -> None:
    user, project = stores(tmp_path)
    user.directory.mkdir()
    (user.directory / ".memory-write.lock").write_text(str(os.getpid()), encoding="ascii")
    tool = ManageMemoryTool(user, project, lambda _: None)

    locked = await tool.execute(create_args())
    assert locked.is_error is True and "未写入" in locked.content

    (user.directory / ".memory-write.lock").unlink()

    def fail(_actions):
        raise OSError("disk full")

    monkeypatch.setattr(user, "apply_locked", fail)
    failed = await tool.execute(create_args())
    assert failed.is_error is True and "未写入" in failed.content
    assert "disk full" not in failed.content
