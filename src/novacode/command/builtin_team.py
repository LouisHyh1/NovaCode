"""`/team` 本地管理命令。"""

from novacode.team.backend import new_backend

_USAGE = "用法: /team list|info <name>|delete <name> [--force]|kill <member>"


async def handle_team(ui) -> None:
    manager = getattr(ui, "team_mgr", None)
    if manager is None:
        return
    parts = ui.command_args().split()
    if not parts:
        raise ValueError(_USAGE)
    command, rest = parts[0], parts[1:]
    if command == "list":
        if rest:
            raise ValueError("用法: /team list")
        teams = manager.list()
        if not teams:
            ui.println("暂无 Team")
            return
        lines = []
        for team in teams:
            teammates = [member for member in team.members if member.name != "lead"]
            active = sum(member.is_active is not False for member in teammates)
            lines.append(
                f"{team.sanitized_name}  {team.backend.value}  "
                f"{len(team.members)} 成员  [{active}/{len(teammates)}] 活跃"
            )
        ui.println("\n".join(lines))
        return
    if command == "info":
        if len(rest) != 1:
            raise ValueError("用法: /team info <name>")
        team = manager.get(rest[0])
        if team is None:
            raise ValueError(f"Team 不存在: {rest[0]}")
        lines = [
            f"Team: {team.sanitized_name}",
            f"backend: {team.backend.value}",
            f"config: {team.config_path}",
        ]
        lines.extend(
            f"- {member.name} id={member.agent_id} backend={member.backend_type.value} "
            f"active={member.is_active} pane={member.pane_id or '-'} "
            f"worktree={member.worktree_path or '-'}"
            for member in team.members
        )
        ui.println("\n".join(lines))
        return
    if command == "delete":
        if not rest or len(rest) > 2 or (len(rest) == 2 and rest[1] != "--force"):
            raise ValueError("用法: /team delete <name> [--force]")
        await manager.delete(rest[0], "--force" in rest)
        ui.println(f"Team 已删除: {rest[0]}")
        return
    if command == "kill":
        if len(rest) != 1:
            raise ValueError("用法: /team kill <member>")
        for team in manager.list():
            member = team.member_by_name(rest[0])
            if member is None or member.name == "lead":
                continue
            backend = new_backend(member.backend_type, task_mgr=manager.task_mgr)
            await backend.kill(member.pane_id, member.agent_id)
            await manager._cleanup_member_resources(team, member)
            await team.remove_member(member.name)
            manager.registry.unregister(member.name)
            ui.println(f"Team 成员已终止: {member.name}")
            return
        raise ValueError(f"Team 成员不存在: {rest[0]}")
    raise ValueError(f"未知 Team 子命令: {command}。{_USAGE}")
