"""`/worktree` 本地管理命令。"""

from novacode.command.ui import UI

_USAGE = "用法: /worktree create|list|enter|exit|remove ..."


async def handle_worktree(ui: UI) -> None:
    accessor = ui.worktree_accessor()
    if accessor is None:
        ui.error("当前目录未启用 Worktree 管理")
        return
    parts = ui.command_args().split()
    if not parts:
        raise ValueError(_USAGE)
    command, rest = parts[0], parts[1:]

    if command == "create":
        if len(rest) != 1:
            raise ValueError("用法: /worktree create <slug>")
        path, branch = await accessor.create(rest[0])
        ui.println(f"Worktree 已创建: {path} (分支 {branch})")
        return
    if command == "list":
        if rest:
            raise ValueError("用法: /worktree list")
        items = accessor.list()
        if not items:
            ui.println("暂无 Worktree")
            return
        lines = []
        for item in items:
            labels = []
            if item.active:
                labels.append("active")
            if item.manual:
                labels.append("manual")
            suffix = f"  [{' '.join(labels)}]" if labels else ""
            lines.append(f"{item.name}  {item.path}  {item.branch}{suffix}")
        ui.println("\n".join(lines))
        return
    if command == "enter":
        if len(rest) != 1:
            raise ValueError("用法: /worktree enter <slug>")
        path = await accessor.enter(rest[0])
        ui.println(f"已进入 {rest[0]}: {path}")
        return
    if command == "exit":
        unknown = set(rest) - {"--remove", "--discard"}
        if unknown:
            raise ValueError(f"未知参数: {', '.join(sorted(unknown))}")
        removed = await accessor.exit(
            "remove" if "--remove" in rest else "keep",
            "--discard" in rest,
        )
        ui.println("已退出并删除 Worktree" if removed else "已退出 Worktree")
        return
    if command == "remove":
        if (
            not rest
            or rest[0].startswith("--")
            or len(rest) > 2
            or any(x != "--discard" for x in rest[1:])
        ):
            raise ValueError("用法: /worktree remove <slug> [--discard]")
        await accessor.remove(rest[0], "--discard" in rest[1:])
        ui.println(f"Worktree 已删除: {rest[0]}")
        return
    raise ValueError(f"未知 Worktree 子命令: {command}。{_USAGE}")
