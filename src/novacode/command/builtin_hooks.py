"""`/hooks` 本地命令。"""

from novacode.command.ui import UI


async def handle_hooks(ui: UI) -> None:
    rules = ui.hook_rules()
    if not rules:
        ui.println("No hooks loaded.")
        return
    grouped = {}
    for rule in rules:
        grouped.setdefault(rule.event, []).append(rule)
    lines: list[str] = []
    for event_rules in grouped.values():
        for rule in event_rules:
            flags = "".join(
                flag
                for enabled, flag in (
                    (rule.only_once, " [once]"),
                    (rule.asyncio_mode, " [async]"),
                )
                if enabled
            )
            lines.append(f"  {rule.name}  {rule.event.value}  {rule.action.type.value}{flags}")
    lines.append(f"Loaded from: {', '.join(ui.hook_sources())}")
    ui.println("\n".join(lines))
