"""把已加载 Skill 注册为斜杠命令。"""

import asyncio
from weakref import WeakKeyDictionary

from novacode.command.command import Command, Kind
from novacode.command.registry import Registry

_REGISTERED_SKILL_NAMES: set[str] = set()
_REGISTRY_SKILLS: WeakKeyDictionary[Registry, set[str]] = WeakKeyDictionary()
_REGISTRY_SHADOWS: WeakKeyDictionary[Registry, dict[str, Command]] = WeakKeyDictionary()


def remove_skill_commands(registry: Registry) -> None:
    names = _REGISTRY_SKILLS.pop(registry, set())
    shadows = _REGISTRY_SHADOWS.pop(registry, {})
    for name in names:
        registry.unregister(name)
        shadowed = shadows.get(name)
        if shadowed is not None:
            registry.register(shadowed)
    _REGISTERED_SKILL_NAMES.difference_update(names)


def register_skill_commands(registry: Registry, loader, executor) -> None:
    remove_skill_commands(registry)
    registered: set[str] = set()
    shadows: dict[str, Command] = {}
    for name, description in loader.get_catalog():
        skill = loader.get(name)
        if skill is None:
            continue

        async def handle(ui, skill_name=name) -> None:
            current = loader.get(skill_name)
            if current is None:
                ui.error(f"Skill '{skill_name}' 不存在")
                return
            args = ui.command_args()
            if current.mode == "inline":
                executor.execute_inline(current, args)
                trigger = args or f"执行 Skill：{skill_name}"
                await ui.inject_and_send(f"/{skill_name}", trigger)
                return

            async def run_fork() -> None:
                result = await executor.execute_fork(current, args)
                request = f"/{skill_name}" + (f" {args}" if args else "")
                await ui.append_assistant_message(result, request)

            asyncio.create_task(run_fork())

        command = Command(name, f"{description} [skill]", Kind.PROMPT, handle)
        shadowed = registry.register(command, replace=True)
        if shadowed is not None:
            shadows[name] = shadowed
        registered.add(name)
        _REGISTERED_SKILL_NAMES.add(name)
    _REGISTRY_SKILLS[registry] = registered
    _REGISTRY_SHADOWS[registry] = shadows
