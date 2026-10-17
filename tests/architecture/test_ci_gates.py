from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def test_ci_runs_only_on_ubuntu_with_all_required_commands() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"]["test"]
    assert set(document["jobs"]) == {"test"}
    assert job["runs-on"] == "ubuntu-latest"
    assert "strategy" not in job

    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )
    for required in (
        "uv sync --locked",
        "uv lock --check",
        "pytest -q",
        "ruff check src tests scripts",
        "ruff format --check src tests scripts",
        "scripts/check_boundaries.py",
        "pytest -q tests/architecture",
        "python -m novacode --version",
        "python -m novacode --help",
    ):
        assert required in commands
