import logging
from pathlib import Path

import pytest

from novacode.instructions import InstructionLoader


def _loader(tmp_path: Path) -> tuple[InstructionLoader, Path, Path]:
    project = tmp_path / "project"
    user_root = tmp_path / "home" / ".novacode"
    project.mkdir(parents=True)
    user_root.mkdir(parents=True)
    return InstructionLoader(project, user_root), project, user_root


def test_loads_four_layers_in_low_to_high_priority_order(tmp_path: Path) -> None:
    loader, project, user_root = _loader(tmp_path)
    (user_root / "NOVACODE.md").write_text("user", encoding="utf-8")
    (project / "NOVACODE.md").write_text("project", encoding="utf-8")
    (project / ".novacode").mkdir()
    (project / ".novacode" / "NOVACODE.md").write_text("private", encoding="utf-8")
    (project / "NOVACODE.local.md").write_text("local", encoding="utf-8")

    assert loader.load() == "user\n---\nproject\n---\nprivate\n---\nlocal"


def test_missing_and_empty_layers_are_skipped_without_extra_separator(tmp_path: Path) -> None:
    loader, project, user_root = _loader(tmp_path)
    (user_root / "NOVACODE.md").write_text("", encoding="utf-8")
    (project / "NOVACODE.local.md").write_text("local", encoding="utf-8")

    assert loader.load() == "local"


def test_only_standalone_at_line_expands_relative_to_declaring_file(tmp_path: Path) -> None:
    loader, project, _ = _loader(tmp_path)
    rules = project / "rules"
    rules.mkdir()
    (rules / "style.md").write_text("use black", encoding="utf-8")
    (project / "NOVACODE.md").write_text(
        "before\n@rules/style.md\nemail @rules/style.md\nafter",
        encoding="utf-8",
    )

    assert loader.load() == "before\nuse black\nemail @rules/style.md\nafter"


def test_reference_depth_includes_fifth_file_but_skips_its_child(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader, project, _ = _loader(tmp_path)
    for depth in range(1, 7):
        content = f"depth-{depth}"
        if depth < 6:
            content += f"\n@{depth + 1}.md"
        (project / f"{depth}.md").write_text(content, encoding="utf-8")
    (project / "NOVACODE.md").write_text("@1.md", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = loader.load()

    assert result == "depth-1\ndepth-2\ndepth-3\ndepth-4\ndepth-5"
    assert "reference depth exceeded" in caplog.text
    assert "depth-6" not in caplog.text


def test_current_chain_detects_cycle_but_allows_shared_file_in_branches(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader, project, _ = _loader(tmp_path)
    (project / "NOVACODE.md").write_text("@a.md\n@b.md", encoding="utf-8")
    (project / "a.md").write_text("A\n@shared.md\n@NOVACODE.md", encoding="utf-8")
    (project / "b.md").write_text("B\n@shared.md", encoding="utf-8")
    (project / "shared.md").write_text("shared", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = loader.load()

    assert result == "A\nshared\nB\nshared"
    assert "reference cycle" in caplog.text


def test_rejects_parent_traversal_and_symlink_escape(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader, project, _ = _loader(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("TOP SECRET BODY", encoding="utf-8")
    link = project / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    (project / "NOVACODE.md").write_text("@../secret.md\n@linked.md\nsafe", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = loader.load()

    assert result == "safe"
    assert caplog.text.count("outside instruction boundary") == 2
    assert "TOP SECRET BODY" not in caplog.text


def test_binary_and_missing_references_are_local_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader, project, _ = _loader(tmp_path)
    (project / "binary.md").write_bytes(b"before\x00after")
    (project / "NOVACODE.md").write_text(
        "first\n@binary.md\n@missing.md\nlast",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        result = loader.load()

    assert result == "first\nlast"
    assert "binary instruction file" in caplog.text
    assert "instruction file unavailable" in caplog.text
    assert "before" not in caplog.text


def test_user_reference_is_confined_to_user_novacode_directory(tmp_path: Path) -> None:
    loader, _, user_root = _loader(tmp_path)
    outside = user_root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (user_root / "NOVACODE.md").write_text("@../outside.md\nuser-safe", encoding="utf-8")

    assert loader.load() == "user-safe"


def test_does_not_scan_unapproved_fifth_root(tmp_path: Path) -> None:
    loader, project, _ = _loader(tmp_path)
    (project / "AGENTS.md").write_text("not loaded", encoding="utf-8")

    assert loader.load() == ""
