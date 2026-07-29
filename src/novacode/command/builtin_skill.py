"""Skill 管理命令。"""

from collections.abc import Callable

from novacode.command.command import Command, Kind
from novacode.command.registry import Registry


def register_skill_management(
    registry: Registry,
    loader,
    on_reload: Callable[[], None],
) -> None:
    async def handle_skill(ui) -> None:
        parts = ui.command_args().split()
        action = parts[0] if parts else "list"
        if action == "list":
            rows = [
                f"  {name:<20} {description}  [{loader.get_source_label(name)}]"
                for name, description in loader.get_catalog()
            ]
            ui.println("\n".join(rows) if rows else "没有已加载的 Skill")
            return
        if action == "info" and len(parts) == 2:
            skill = loader.get(parts[1])
            if skill is None:
                ui.error(f"未知 Skill：{parts[1]}")
                return
            ui.println(
                f"name: {skill.name}\n"
                f"description: {skill.description}\n"
                f"mode: {skill.mode}\n"
                f"model: {skill.model or '-'}\n"
                f"context: {skill.context}\n"
                f"source: {loader.get_source_label(skill.name)}\n"
                f"path: {skill.source_path}\n"
                f"directory: {skill.is_directory}"
            )
            return
        if action == "reload" and len(parts) == 1:
            loader.reload()
            on_reload()
            ui.println(f"已重新加载 {len(loader.names())} 个 Skill")
            return
        ui.error("用法：/skill list | /skill info <name> | /skill reload")

    registry.register(Command("skill", "列出、查看或重载 Skill", Kind.LOCAL, handle_skill))
