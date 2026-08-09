from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def test_ci_matrix_and_required_commands_cover_both_platforms() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"]["test"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]

    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )
    for required in (
        "uv sync --locked",
        "uv lock --check",
        "pytest -q",
        "ruff check src tests",
        "ruff format --check src tests",
        "scripts/check_boundaries.py",
        "pytest -q tests/architecture",
        "python -m novacode --version",
        "python -m novacode --help",
    ):
        assert required in commands
