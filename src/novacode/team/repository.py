"""Team 聚合的 versioned JSON Repository。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from novacode.adapters.json_store import AtomicJsonStore, FaultHook, JsonObject, JsonValue
from novacode.adapters.migration import write_migration_backup
from novacode.runtime.errors import (
    ConflictError,
    StateCorruptionError,
    UnsupportedSchemaError,
    ValidationError,
)
from novacode.team.domain import (
    AgentAddress,
    AgentId,
    MemberName,
    TeamId,
    TeamMember,
    TeamState,
)
from novacode.team.ports import TeamMutation

TEAM_SCHEMA_VERSION = 1


class JsonTeamRepository:
    """一个文件对应一个 Team 聚合。"""

    def __init__(self, path: str | Path, *, fault_hook: FaultHook | None = None) -> None:
        self.path = Path(path)
        self.store = AtomicJsonStore(self.path, fault_hook=fault_hook)
        self._recovery_error: StateCorruptionError | None = None

    async def load(self, aggregate_id: TeamId) -> TeamState:
        state = await self.load_existing()
        if state.team_id != aggregate_id:
            raise ConflictError(f"Team ID 不匹配: {aggregate_id}")
        return state

    async def load_existing(self) -> TeamState:
        self._guard_recovery()
        try:
            raw = await self.store.update_if({}, self._migration_candidate)
            if not raw:
                raise KeyError(f"Team 不存在: {self.path}")
            return _decode_team(raw, self.path)
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    async def create(self, state: TeamState) -> TeamState:
        self._guard_recovery()
        _validate_team_state(state)
        created = replace(state, schema_version=TEAM_SCHEMA_VERSION, revision=1)

        def create_if_empty(current: JsonObject) -> JsonObject:
            if current:
                raise ConflictError(f"Team 已存在: {state.team_id}")
            return _encode_team(created)

        try:
            raw = await self.store.transact({}, create_if_empty, self._validate_raw)
            return _decode_team(raw, self.path)
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    async def transact(self, aggregate_id: TeamId, mutation: TeamMutation) -> TeamState:
        self._guard_recovery()
        await self.load_existing()

        def update(current: JsonObject) -> JsonObject:
            if not current:
                raise KeyError(f"Team 不存在: {aggregate_id}")
            state = _decode_team(current, self.path)
            if state.team_id != aggregate_id:
                raise ConflictError(f"Team ID 不匹配: {aggregate_id}")
            candidate = mutation(state)
            if candidate.team_id != aggregate_id:
                raise ValidationError("Team 事务不能修改 Team ID")
            candidate = replace(
                candidate,
                schema_version=TEAM_SCHEMA_VERSION,
                revision=state.revision + 1,
            )
            _validate_team_state(candidate)
            return _encode_team(candidate)

        try:
            raw = await self.store.transact({}, update, self._validate_raw)
            return _decode_team(raw, self.path)
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    def diagnostic(self) -> tuple[str, str] | None:
        if self._recovery_error is None:
            return None
        return self._recovery_error.category, self._recovery_error.path

    def _guard_recovery(self) -> None:
        if self._recovery_error is not None:
            raise self._recovery_error

    def _validate_raw(self, raw: JsonObject) -> None:
        _decode_team(raw, self.path)

    def _migration_candidate(self, current: JsonObject) -> JsonObject | None:
        if not current:
            return None
        schema_version = current.get("schema_version")
        if schema_version is None:
            state = _decode_legacy_team(current, self.path)
            write_migration_backup(self.path)
            return _encode_team(state)
        if isinstance(schema_version, int) and schema_version > TEAM_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                str(self.path),
                detail=f"未知 schema_version: {schema_version}",
            )
        _decode_team(current, self.path)
        return None


def _encode_team(state: TeamState) -> JsonObject:
    members: list[JsonValue] = [
        {
            "name": str(member.address.member_name),
            "agent_id": str(member.agent_id),
            "agent_type": member.agent_type,
            "model": member.model,
            "worktree_path": member.worktree_path,
            "branch": member.branch,
            "backend_type": member.backend_type,
            "pane_id": member.pane_id,
            "is_active": member.active,
            "plan_mode_required": member.plan_mode_required,
            "session_dir": member.session_dir,
        }
        for member in state.members
    ]
    return {
        "schema_version": state.schema_version,
        "revision": state.revision,
        "team_id": str(state.team_id),
        "name": state.name,
        "sanitized_name": state.sanitized_name or state.name,
        "lead_agent_id": str(state.lead_agent_id),
        "backend": state.backend,
        "description": state.description,
        "created_at": state.created_at,
        "permission_mode": state.permission_mode,
        "members": members,
    }


def _decode_team(raw: JsonObject, path: Path) -> TeamState:
    schema_version = raw.get("schema_version")
    revision = raw.get("revision")
    team_id = raw.get("team_id")
    name = raw.get("name")
    members = raw.get("members")
    if not isinstance(schema_version, int) or schema_version != TEAM_SCHEMA_VERSION:
        raise StateCorruptionError(str(path), detail="schema_version 无效")
    if not isinstance(revision, int) or revision < 1:
        raise StateCorruptionError(str(path), detail="revision 无效")
    if not isinstance(team_id, str) or not team_id:
        raise StateCorruptionError(str(path), detail="team_id 无效")
    if not isinstance(name, str) or not name:
        raise StateCorruptionError(str(path), detail="name 无效")
    if not isinstance(members, list):
        raise StateCorruptionError(str(path), detail="members 无效")
    decoded_members = tuple(_decode_member(item, TeamId(team_id), path) for item in members)
    state = TeamState(
        TeamId(team_id),
        name,
        decoded_members,
        schema_version=schema_version,
        revision=revision,
        sanitized_name=_optional_string(raw, "sanitized_name", name, path),
        lead_agent_id=AgentId(_optional_string(raw, "lead_agent_id", "lead", path)),
        backend=_optional_string(raw, "backend", "in-process", path),
        description=_optional_string(raw, "description", "", path),
        created_at=_optional_number(raw, "created_at", 0.0, path),
        permission_mode=_optional_string(raw, "permission_mode", "default", path),
    )
    try:
        _validate_team_state(state)
    except ValidationError as exc:
        raise StateCorruptionError(str(path), detail=str(exc)) from exc
    return state


def _decode_member(value: JsonValue, team_id: TeamId, path: Path) -> TeamMember:
    if not isinstance(value, dict):
        raise StateCorruptionError(str(path), detail="member 结构无效")
    name = value.get("name")
    agent_id = value.get("agent_id")
    active = value.get("is_active")
    if not isinstance(name, str) or not name:
        raise StateCorruptionError(str(path), detail="member name 无效")
    if not isinstance(agent_id, str) or not agent_id:
        raise StateCorruptionError(str(path), detail="member agent_id 无效")
    if active is not None and not isinstance(active, bool):
        raise StateCorruptionError(str(path), detail="member is_active 无效")
    return TeamMember(
        AgentAddress(team_id, MemberName(name)),
        AgentId(agent_id),
        active,
        agent_type=_optional_string(value, "agent_type", "", path),
        model=_optional_string(value, "model", "", path),
        worktree_path=_optional_string(value, "worktree_path", "", path),
        branch=_optional_string(value, "branch", "", path),
        backend_type=_optional_string(value, "backend_type", "in-process", path),
        pane_id=_optional_string(value, "pane_id", "", path),
        plan_mode_required=_optional_bool(value, "plan_mode_required", False, path),
        session_dir=_optional_string(value, "session_dir", "", path),
    )


def _validate_team_state(state: TeamState) -> None:
    names = [member.address.member_name for member in state.members]
    agent_ids = [member.agent_id for member in state.members]
    if any(member.address.team_id != state.team_id for member in state.members):
        raise ValidationError("成员地址不属于目标 Team")
    if len(set(names)) != len(names):
        raise ValidationError("同一 Team 内存在重复 Member Name")
    if len(set(agent_ids)) != len(agent_ids):
        raise ValidationError("同一 Team 内存在重复 Agent ID")


def _decode_legacy_team(raw: JsonObject, path: Path) -> TeamState:
    name = raw.get("name")
    sanitized_name = raw.get("sanitized_name")
    backend = raw.get("backend")
    members = raw.get("members")
    if not isinstance(name, str) or not name:
        raise StateCorruptionError(str(path), detail="旧 Team name 无效")
    if not isinstance(sanitized_name, str) or not sanitized_name:
        raise StateCorruptionError(str(path), detail="旧 Team sanitized_name 无效")
    if not isinstance(backend, str) or not backend:
        raise StateCorruptionError(str(path), detail="旧 Team backend 无效")
    if not isinstance(members, list):
        raise StateCorruptionError(str(path), detail="旧 Team members 无效")
    team_id = stable_team_id_for_path(path)
    state = TeamState(
        team_id,
        name,
        tuple(_decode_member(item, team_id, path) for item in members),
        schema_version=TEAM_SCHEMA_VERSION,
        revision=1,
        sanitized_name=sanitized_name,
        lead_agent_id=AgentId(_optional_string(raw, "lead_agent_id", "lead", path)),
        backend=backend,
        description=_optional_string(raw, "description", "", path),
        created_at=_optional_number(raw, "created_at", 0.0, path),
        permission_mode=_optional_string(raw, "permission_mode", "default", path),
    )
    try:
        _validate_team_state(state)
    except ValidationError as exc:
        raise StateCorruptionError(str(path), detail=str(exc)) from exc
    return state


def _optional_string(
    raw: dict[str, JsonValue],
    key: str,
    default: str,
    path: Path,
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise StateCorruptionError(str(path), detail=f"{key} 无效")
    return value


def _optional_number(
    raw: dict[str, JsonValue],
    key: str,
    default: float,
    path: Path,
) -> float:
    value = raw.get(key, default)
    if not isinstance(value, int | float):
        raise StateCorruptionError(str(path), detail=f"{key} 无效")
    return float(value)


def _optional_bool(
    raw: dict[str, JsonValue],
    key: str,
    default: bool,
    path: Path,
) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise StateCorruptionError(str(path), detail=f"{key} 无效")
    return value


def stable_team_id_for_path(path: str | Path) -> TeamId:
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"novacode-team:{Path(path).resolve()}")
    return TeamId(f"team-{stable.hex}")
