"""Session resume option rendering."""

from textual.widgets.option_list import Option

from novacode.session import SessionInfo


def format_session_option(info: SessionInfo) -> str:
    size = f"{info.file_size / 1024:.1f} KiB"
    activity = info.last_activity.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{info.title}  ·  {activity}  ·  {info.model}  ·  {size}  [{info.session_id}]"


def build_resume_options(sessions: list[SessionInfo]) -> list[Option]:
    return [Option(format_session_option(info), id=info.session_id) for info in sessions]
