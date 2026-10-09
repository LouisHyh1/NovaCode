"""记录本阶段可复跑的验证命令、退出码和版本身份。"""

import importlib.metadata
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import novacode

ROOT = Path(__file__).resolve().parents[5]
EVIDENCE = Path(__file__).resolve().parent
UV = "/home/louishyh/.local/bin/uv"
ENV = {**os.environ, "UV_DEFAULT_INDEX": "https://pypi.tuna.tsinghua.edu.cn/simple"}
COMMANDS = {
    "lock": [UV, "lock", "--check"],
    "lint": [
        UV,
        "run",
        "--locked",
        "ruff",
        "check",
        "src",
        "tests",
        "scripts",
        "evaluation/examples/tmux_observation_probe.py",
    ],
    "format": [
        UV,
        "run",
        "--locked",
        "ruff",
        "format",
        "--check",
        "src",
        "tests",
        "scripts",
        "evaluation/examples/tmux_observation_probe.py",
    ],
    "mypy-evaluation": [
        UV,
        "run",
        "--locked",
        "mypy",
        "--strict",
        "--follow-imports=silent",
        "src/novacode/evaluation",
        "src/novacode/privacy.py",
        "src/novacode/llm/metadata.py",
    ],
    "complexity-evaluation": [
        UV,
        "run",
        "--locked",
        "ruff",
        "check",
        "--select",
        "C901",
        "src/novacode/evaluation",
        "src/novacode/privacy.py",
        "src/novacode/llm/metadata.py",
    ],
    "boundaries": [UV, "run", "--locked", "python", "scripts/check_boundaries.py"],
    "pytest": [UV, "run", "--locked", "pytest", "-q"],
    "startup-version": [UV, "run", "--locked", "python", "-m", "novacode", "--version"],
    "startup-help": [UV, "run", "--locked", "python", "-m", "novacode", "--help"],
}


def main():
    results = []
    for name, command in COMMANDS.items():
        with (EVIDENCE / (name + ".txt")).open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command, cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT
            )
        results.append({"check": name, "command": command, "exit_code": result.returncode})
        print(name, result.returncode, flush=True)
    (EVIDENCE / "quality-results.json").write_text(
        json.dumps(
            {
                "environment_override": {"UV_DEFAULT_INDEX": ENV["UV_DEFAULT_INDEX"]},
                "checks": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    lock_version = next(p["version"] for p in lock["package"] if p["name"] == "novacode")
    installed = importlib.metadata.version("novacode")
    identity = {
        "pyproject": project,
        "runtime": novacode.__version__,
        "lock": lock_version,
        "installed": installed,
        "python": sys.version,
        "sdks": {n: importlib.metadata.version(n) for n in ("anthropic", "openai", "httpx")},
    }
    (EVIDENCE / "version-identity.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert len({project, novacode.__version__, lock_version, installed}) == 1
    assert all(r["exit_code"] == 0 for r in results), results


if __name__ == "__main__":
    main()
