import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from novacode import prompt
from novacode.agent import Agent
from novacode.command import Command, Kind, NopUI
from novacode.command import Registry as CommandRegistry
from novacode.command.builtin_skill import register_skill_management
from novacode.command.skill_register import register_skill_commands, remove_skill_commands
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Mode
from novacode.skills import (
    SkillDef,
    SkillExecutor,
    SkillLoader,
    SkillParseError,
    install_skill,
    parse_frontmatter,
    parse_skill_file,
    parse_skill_url,
    substitute_arguments,
)
from novacode.tool import Registry as ToolRegistry
from novacode.tool.load_skill import LoadSkill


def _skill(name: str = "test-skill", body: str = "Echo hello", **meta) -> str:
    fields = {
        "name": name,
        "description": "A test skill",
        **meta,
    }
    frontmatter = "\n".join(f"{key}: {value}" for key, value in fields.items())
    return f"---\n{frontmatter}\n---\n{body}\n"


def test_parse_skill_and_substitute_arguments(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        _skill(body="Review $ARGUMENTS twice: $ARGUMENTS", mode="fork", context="recent")
    )

    meta, body = parse_frontmatter(path.read_text())
    skill = parse_skill_file(path)

    assert meta["name"] == "test-skill"
    assert body.startswith("Review")
    assert skill.mode == "fork" and skill.context == "recent"
    assert skill.is_directory
    assert substitute_arguments(skill.prompt_body, "src/").count("src/") == 2
    assert substitute_arguments("unchanged", "x") == "unchanged"


@pytest.mark.parametrize(
    "raw,match",
    [
        ("name: x", "opening"),
        ("---\nname: x", "unclosed"),
        ("---\n[x\n---\nbody", "YAML"),
        ("---\n- x\n---\nbody", "mapping"),
    ],
)
def test_parse_frontmatter_rejects_invalid_input(raw: str, match: str) -> None:
    with pytest.raises(SkillParseError, match=match):
        parse_frontmatter(raw)


@pytest.mark.parametrize(
    "raw,match",
    [
        (_skill(name="Bad_Name"), "name"),
        ("---\nname: valid\n---\nbody", "description"),
        (_skill(mode="other"), "mode"),
        (_skill(context="other"), "context"),
    ],
)
def test_parse_skill_validates_metadata(tmp_path: Path, raw: str, match: str) -> None:
    path = tmp_path / "skill.md"
    path.write_text(raw)
    with pytest.raises(SkillParseError, match=match):
        parse_skill_file(path)


def test_parse_nonexistent_skill() -> None:
    with pytest.raises(SkillParseError, match="cannot read"):
        parse_skill_file("does-not-exist.md")


def test_loader_priority_layout_catalog_and_hot_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user = tmp_path / "user-skills"
    project = tmp_path / "project"
    monkeypatch.setattr("novacode.skills.loader.USER_SKILLS_DIR", str(user))
    (user / "shared").mkdir(parents=True)
    (user / "shared" / "SKILL.md").write_text(_skill("shared", "user"))
    project_skills = project / ".novacode" / "skills"
    project_skills.mkdir(parents=True)
    project_file = project_skills / "shared.md"
    project_file.write_text(_skill("shared", "project"))
    (project_skills / "single.md").write_text(_skill("single", "one"))
    (project_skills / "bad.md").write_text("not frontmatter")

    loader = SkillLoader(project)
    loader.load_all()

    assert loader.names() == ["shared", "single"]
    assert loader.get("shared").prompt_body.strip() == "project"  # type: ignore[union-attr]
    assert loader.get_source_label("shared") == "project"
    assert loader.get_source_label("missing") == "unknown"
    assert loader.get_catalog() == [("shared", "A test skill"), ("single", "A test skill")]
    assert "Skipping project skill" in caplog.text

    project_file.write_text(_skill("shared", "updated"))
    assert loader.get("shared").prompt_body.strip() == "updated"  # type: ignore[union-attr]
    project_file.write_text("broken")
    assert loader.get("shared").prompt_body.strip() == "updated"  # type: ignore[union-attr]
    assert loader.get("missing") is None

    (project_skills / "single.md").unlink()
    loader.reload()
    assert loader.names() == ["shared"]


@pytest.mark.asyncio
async def test_load_skill_tool_and_agent_environment(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".novacode" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "test.md").write_text(_skill(body="Pinned SOP"))
    loader = SkillLoader(tmp_path)
    loader.load_all()
    agent = MagicMock()
    tool = LoadSkill()

    assert (await tool.execute('{"name":"test-skill"}')).is_error
    tool.set_loader(loader)
    tool.set_agent(agent)
    result = await tool.execute('{"name":"test-skill"}')

    assert not result.is_error and "activated" in result.content
    agent.activate_skill.assert_called_once_with("test-skill", "Pinned SOP\n")
    assert (await tool.execute('{"name":"missing"}')).is_error
    assert tool.read_only and tool.category == "read"

    env = prompt.build_environment_context(
        "Working Directory: /tmp",
        {"test-skill": "Pinned SOP"},
        "## Available Skills",
    )
    assert "## Active Skills" in env and "Pinned SOP" in env
    assert "Active Skills" not in prompt.build_environment_context("base")


class FakeProvider:
    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.requests: list[Request] = []
        self.index = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        script = self.scripts[self.index]
        self.index += 1
        for event in script:
            yield event
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_agent_sees_loaded_skill_on_next_iteration(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".novacode" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "test.md").write_text(_skill(body="Pinned SOP"))
    loader = SkillLoader(tmp_path)
    loader.load_all()
    tool = LoadSkill()
    registry = ToolRegistry()
    registry.register(tool)
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[ToolCall("1", "LoadSkill", '{"name":"test-skill"}')])],
            [StreamEvent(text="done")],
        ]
    )
    agent = Agent(provider, registry)
    tool.set_loader(loader)
    tool.set_agent(agent)
    agent.set_skill_catalog("## Available Skills\n- test-skill: A test skill")
    conv = Conversation()
    conv.add_user("use the test skill")

    _ = [event async for event in agent.run(conv, Mode.BYPASS, asyncio.Event())]

    assert "Available Skills" in provider.requests[0].system.environment
    assert "Pinned SOP" in provider.requests[1].system.environment
    agent.clear_active_skills()
    assert agent.active_skills == {}


@pytest.mark.asyncio
async def test_fork_executor_isolates_main_conversation() -> None:
    provider = FakeProvider([[StreamEvent(text="fork result")]])
    agent = Agent(provider, ToolRegistry())
    main = Conversation()
    main.add_user("main message")
    executor = SkillExecutor(agent, conversation=lambda: main)
    skill = SkillDef("forked", "Fork", "Do work", mode="fork", context="recent")

    result = await executor.execute_fork(skill, "")

    assert result == "fork result"
    assert [message.content for message in main.messages()] == ["main message"]
    assert provider.requests[0].messages[-1].content == "Do work"


class RecordingUI(NopUI):
    def __init__(self, args: str = "") -> None:
        self.args = args
        self.injections: list[tuple[str, str]] = []
        self.messages: list[str] = []

    def command_args(self) -> str:
        return self.args

    async def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.injections.append((display_label, preset_prompt))

    def println(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_skill_command_overrides_and_restores_builtin() -> None:
    registry = CommandRegistry()

    async def old_handler(ui) -> None:
        pass

    old = Command("review", "old", Kind.PROMPT, old_handler)
    registry.register(old)
    loader = MagicMock()
    skill = SkillDef("review", "New", "Review $ARGUMENTS")
    loader.get_catalog.return_value = [("review", "New")]
    loader.get.return_value = skill
    executor = MagicMock()
    ui = RecordingUI("src")

    register_skill_commands(registry, loader, executor)
    command = registry.lookup("review")
    assert command is not old and command.description.endswith("[skill]")  # type: ignore[union-attr]
    await command.handler(ui)  # type: ignore[union-attr]
    executor.execute_inline.assert_called_once_with(skill, "src")
    assert ui.injections == [("/review", "src")]

    remove_skill_commands(registry)
    assert registry.lookup("review") is old


def test_parse_supported_skill_urls() -> None:
    skills_sh = parse_skill_url("https://skills.sh/acme/repo/my-skill")
    assert skills_sh.path == "my-skill" and skills_sh.kind == "skills_sh"
    www_skills_sh = parse_skill_url("https://www.skills.sh/acme/repo/my-skill")
    assert www_skills_sh == skills_sh
    tree = parse_skill_url("https://github.com/acme/repo/tree/main/skills/my-skill")
    assert (tree.owner, tree.repo, tree.ref, tree.path) == (
        "acme",
        "repo",
        "main",
        "skills/my-skill",
    )
    raw = parse_skill_url(
        "https://raw.githubusercontent.com/acme/repo/main/skills/my-skill/SKILL.md"
    )
    assert raw.path == "skills/my-skill"
    with pytest.raises(ValueError, match="unsupported"):
        parse_skill_url("https://example.com/skill")


class FakeResponse:
    def __init__(self, data) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self.data


class FakeHTTPClient:
    def __init__(self, manifest: str) -> None:
        encoded = base64.b64encode(manifest.encode()).decode()
        self.responses = [
            FakeResponse(
                [
                    {
                        "type": "file",
                        "name": "SKILL.md",
                        "path": "skills/demo/SKILL.md",
                        "size": len(manifest),
                        "url": "https://api.github.com/repos/acme/repo/contents/skills/demo/SKILL.md",
                    }
                ]
            ),
            FakeResponse({"encoding": "base64", "content": encoded}),
        ]

    async def get(self, url: str, params=None):
        return self.responses.pop(0)


class FakeSkillsClient:
    def __init__(self, manifest: str) -> None:
        encoded_manifest = base64.b64encode(manifest.encode()).decode()
        encoded_example = base64.b64encode(b"example").decode()
        self.responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse(
                {
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": "skills/demo/SKILL.md"},
                    ],
                }
            ),
            FakeResponse(
                [
                    {
                        "type": "file",
                        "name": "SKILL.md",
                        "path": "skills/demo/SKILL.md",
                        "size": len(manifest),
                        "url": "https://api.github.com/repos/acme/repo/contents/skills/demo/SKILL.md",
                    },
                    {
                        "type": "dir",
                        "name": "references",
                        "path": "skills/demo/references",
                    },
                ]
            ),
            FakeResponse({"encoding": "base64", "content": encoded_manifest}),
            FakeResponse(
                [
                    {
                        "type": "file",
                        "name": "example.txt",
                        "path": "skills/demo/references/example.txt",
                        "size": 7,
                        "url": "https://api.github.com/repos/acme/repo/contents/skills/demo/references/example.txt",
                    }
                ]
            ),
            FakeResponse({"encoding": "base64", "content": encoded_example}),
        ]

    async def get(self, url: str, params=None):
        return self.responses.pop(0)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_install_skill_downloads_to_atomic_destination(tmp_path: Path) -> None:
    name = await install_skill(
        "https://github.com/acme/repo/tree/main/skills/demo",
        tmp_path,
        client=FakeHTTPClient(_skill("demo")),  # type: ignore[arg-type]
    )

    assert name == "demo"
    assert (tmp_path / "demo" / "SKILL.md").is_file()
    assert not list(tmp_path.glob(".skill-staging-*"))

    with pytest.raises(FileExistsError):
        await install_skill(
            "https://github.com/acme/repo/tree/main/skills/demo",
            tmp_path,
            client=FakeHTTPClient(_skill("demo")),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_install_skill_uses_skills_sh_file_tree(tmp_path: Path) -> None:
    name = await install_skill(
        "https://skills.sh/acme/repo/demo",
        tmp_path,
        client=FakeSkillsClient(_skill("demo")),  # type: ignore[arg-type]
    )

    assert name == "demo"
    assert (tmp_path / "demo" / "references" / "example.txt").read_text() == "example"


@pytest.mark.asyncio
async def test_install_skill_uses_github_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    client = FakeSkillsClient(_skill("demo"))

    def make_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr("novacode.skills.install.httpx.AsyncClient", make_client)

    await install_skill("https://skills.sh/acme/repo/demo", tmp_path)

    assert captured["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_skill_reload_discovers_manually_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_skills = tmp_path / "user-skills"
    monkeypatch.setattr("novacode.skills.loader.USER_SKILLS_DIR", str(user_skills))
    loader = SkillLoader(tmp_path / "project")
    loader.load_all()
    (user_skills / "manual").mkdir(parents=True)
    (user_skills / "manual" / "SKILL.md").write_text(_skill("manual"))
    registry = CommandRegistry()
    on_reload = MagicMock()
    register_skill_management(registry, loader, on_reload)
    ui = RecordingUI("reload")

    command = registry.lookup("skill")
    assert command is not None
    await command.handler(ui)

    assert loader.names() == ["manual"]
    on_reload.assert_called_once_with()
    assert ui.messages == ["已重新加载 1 个 Skill"]


@pytest.mark.asyncio
async def test_install_skill_rejects_skills_sh_path_traversal(tmp_path: Path) -> None:
    client = FakeSkillsClient(_skill("demo"))
    client.responses[1] = FakeResponse(
        {
            "truncated": False,
            "tree": [{"type": "blob", "path": "../demo/SKILL.md"}],
        }
    )

    with pytest.raises(ValueError, match="invalid skill path"):
        await install_skill(
            "https://skills.sh/acme/repo/demo",
            tmp_path,
            client=client,  # type: ignore[arg-type]
        )

    assert not (tmp_path.parent / "outside.txt").exists()
