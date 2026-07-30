import pytest

from novacode.config import Config, FeaturesConfig
from novacode.coordinator import allowed_tools, is_enabled
from novacode.team.backend import SpawnRequest
from novacode.team.backend.detect import detect
from novacode.team.backend.tmux import build_member_cmd
from novacode.team.types import BackendType


def test_backend_detection_priority(monkeypatch) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect() is BackendType.TMUX
    monkeypatch.delenv("TMUX")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr("shutil.which", lambda name: "/bin/it2" if name == "it2" else None)
    assert detect() is BackendType.ITERM2
    monkeypatch.delenv("TERM_PROGRAM")
    monkeypatch.setattr("shutil.which", lambda name: "/bin/tmux" if name == "tmux" else None)
    assert detect() is BackendType.TMUX
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect() is BackendType.IN_PROCESS


def test_tmux_member_command_contains_agent_id_not_prompt() -> None:
    request = SpawnRequest(
        "demo",
        "alice",
        "agent-123",
        "/repo/wt",
        "/repo/session.jsonl",
        "general-purpose",
        "",
        "secret initial prompt",
        True,
    )
    command = build_member_cmd(request)
    assert command[command.index("--agent-id") + 1] == "agent-123"
    assert "secret initial prompt" not in command
    assert "--plan-mode" in command


@pytest.mark.parametrize(
    ("feature", "environment", "expected"),
    [(False, "", False), (False, "1", False), (True, "", False), (True, "yes", True)],
)
def test_coordinator_double_lock(monkeypatch, feature, environment, expected) -> None:
    config = Config(features=FeaturesConfig(coordinator_mode=feature))
    monkeypatch.setenv("MEWCODE_COORDINATOR_MODE", environment)
    assert is_enabled(config) is expected
    assert "bash" in allowed_tools()
    assert "write_file" not in allowed_tools()
    assert "edit_file" not in allowed_tools()
