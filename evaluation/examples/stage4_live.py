"""第四阶段代表题复跑：固定资产、有限先导预算和真实 tmux 产品入口。"""

import argparse
import asyncio
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from novacode.config import load
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import Asset, EvaluationTask, json_text
from novacode.evaluation.runner import command, run_task

REPO = Path(__file__).resolve().parents[2]
CACHE = Path.home() / ".cache/novacode-evaluation"
GRADER = CACHE / "stage1/grader-venv/bin/python"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def setup(root, usage):
    root.mkdir(exist_ok=False)
    wheelhouse = root / "wheels"
    wheelhouse.mkdir()
    for wheel in (CACHE / "stage1/wheels").glob("*.whl"):
        if not wheel.name.startswith("novacode-"):
            shutil.copy2(wheel, wheelhouse)
    subprocess.run(
        [str(Path.home() / ".local/bin/uv"), "build", "--wheel", "--out-dir", str(wheelhouse)],
        cwd=REPO,
        check=True,
    )
    archive = root / "agent-runtime.tar"
    subprocess.run(
        [
            sys.executable,
            "evaluation/preflight/pack_agent_runtime.py",
            "--python-home",
            str(Path.home() / ".local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu"),
            "--uv",
            str(Path.home() / ".local/bin/uv"),
            "--wheelhouse",
            str(wheelhouse),
            "--output",
            str(archive),
        ],
        cwd=REPO,
        check=True,
    )
    prepared = root / "assets"
    result = subprocess.check_output(
        [
            str(GRADER),
            "evaluation/preflight/stage4_judge.py",
            "--prepare",
            "--dataset",
            str(CACHE / "stage1/verified.parquet"),
            "--output",
            str(prepared),
        ],
        cwd=REPO,
        text=True,
    )
    meta = json.loads(result)
    public = prepared / "public"
    hidden = prepared / "hidden"
    save(
        public / "environment.json",
        {
            "schema_version": 1,
            "image": meta["image"],
            "resources": {"cpus": 2, "memory_bytes": 4 * 1024**3, "pids": 256},
            "runtime": {"sha256": digest(archive)},
            "grader_commit": "ad79b850f15e33992e96f03f6e97f05ddf9aa0be",
        },
    )

    def asset(base, name):
        return Asset(name, digest(base / name))

    task = EvaluationTask(
        task_id="stage4-faker-representative",
        candidate_id=meta["instance"],
        source="live",
        suite="development",
        family_id="issue:joke2k/faker#2096",
        problem_keys=("issue:joke2k/faker#2096",),
        repository="joke2k/faker",
        source_revision="b51a86422e10cfd403beb4773e5a2947953e36ec",
        base_commit=meta["base_commit"],
        difficulty="easy",
        category="issue-fix",
        pressure_tags=(),
        budget_category="stage4-representative",
        initial_state=asset(public, "initial.json"),
        public_input=asset(public, "requests.json"),
        environment=asset(public, "environment.json"),
        acceptance_assets=tuple(
            asset(hidden, name) for name in ("acceptance.json", "row.json", "judge.py")
        ),
        acceptance_conditions=("official-tests",),
        allowed_paths=("faker/**", "tests/**"),
    )
    (root / "task.json").write_text(json_text(task) + "\n")
    shutil.copytree(usage, root / "usage-probe")
    raw = json.loads((usage / "usage.json").read_text())
    native = raw[-1]["raw_usage"]
    # 同一内容实际缓存命中 1408：Anthropic input 为 miss，native prompt 为总输入。
    for row in raw[:2]:
        u = row["raw_usage"]
        assert (
            u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
            == native["prompt_tokens"]
        )
    assert raw[1]["raw_usage"]["cache_read_input_tokens"] > 0
    limits = BudgetLimits(
        seconds=240,
        tokens=300_000,
        provider_calls=16,
        tool_calls=30,
        single_request_tokens=40_000,
        cleanup_seconds=20,
    )
    total = replace(limits, seconds=600, tokens=600_000, provider_calls=32, tool_calls=60)
    save(
        root / "pilot.json",
        {
            "campaign_id": root.name,
            "limits": asdict(limits),
            "total_limits": asdict(total),
            "config_id": "eager-schema",
            "allowed_tools": ["read_file", "write_file", "edit_file", "bash", "glob", "grep"],
            "permissions": {
                "allow": [
                    "Write",
                    "Edit",
                    "Bash",
                ],
                "deny": [],
            },
            "instructions": (
                "Fix the issue in the repository. Use the existing project Python and tests. "
                "Do not commit or modify runtime installation. Return a brief summary. "
                "Prefer focused searches; use shell sed for small sections of large files."
            ),
            "usage_rule": {
                "rule_id": "deepseek-anthropic-cache-separate-stage4-v1",
                "protocol": "anthropic",
                "cache_mode": "separate",
                "evidence_sha256": digest(root / "usage-probe/usage.json"),
            },
        },
    )
    (root / "runs").mkdir()


async def tmux_run(output, task_future, request):
    terminal = output / "tui-command.json"
    while not terminal.exists() and not task_future.done():
        await asyncio.sleep(0.25)
    if task_future.done():
        return
    launcher = output / "tui-launcher.py"
    launcher.write_text(
        "import json,subprocess,sys\nfrom pathlib import Path\n"
        "p=Path(sys.argv[1])\nr=subprocess.run(json.loads(p.read_text()))\n"
        "p.with_name('tui-exited').write_text(str(r.returncode))\n"
        "raise SystemExit(r.returncode)\n"
    )
    session = output.parent.parent.name + "-" + output.name
    tmux = ["tmux", "-L", "novacode-stage4"]
    config = output / "tmux.conf"
    config.write_text("set -g remain-on-exit on\n")
    await command(
        *tmux,
        "-f",
        str(config),
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        "160",
        "-y",
        "50",
        shlex.join([sys.executable, str(launcher), str(terminal)]),
    )
    argv = json.loads(terminal.read_text())
    container = argv[argv.index("HOME=/isolated-user") + 1]
    for _ in range(30):
        if (output / "tui-exited").exists():
            break
        await asyncio.sleep(0.5)
        raw = await command(
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            "cat /run/novacode-output/ledger.jsonl 2>/dev/null || true",
        )
        if b'"kind": "tui_initialized"' in raw:
            break
    (output / "request.txt").write_text(request.replace("\r\n", "\n"))
    await command(*tmux, "load-buffer", str(output / "request.txt"))
    await command(*tmux, "paste-buffer", "-r", "-p", "-t", session)
    await command(*tmux, "send-keys", "-t", session, "Enter")
    for _ in range(240):
        if task_future.done():
            break
        await asyncio.sleep(1)
        try:
            raw = await command(
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                "cat /run/novacode-output/ledger.jsonl 2>/dev/null || true",
            )
        except RuntimeError:
            break
        if b'"kind": "agent_run_end"' in raw:
            break
    try:
        pane = await command(*tmux, "capture-pane", "-p", "-S", "-", "-t", session)
        (output / "tmux-pane.txt").write_bytes(pane)
        await command(*tmux, "send-keys", "-t", session, "C-c")
    except RuntimeError:
        pass


async def run(root, name, tui):
    task = EvaluationTask.from_json((root / "task.json").read_text())
    data = json.loads((root / "pilot.json").read_text())
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == "deepseek-v4-flash" and p.protocol == "anthropic"
    )
    cfg = replace(cfg, max_retries=0, timeout=60, max_output_tokens=4096, thinking=True)
    payload = {**data, "provider": asdict(cfg), "run_id": name, "repeat_id": 1}
    output = root / "runs" / name
    future = asyncio.create_task(
        run_task(
            task,
            public_root=root / "assets/public",
            acceptance_root=root / "assets/hidden",
            runtime_archive=root / "agent-runtime.tar",
            payload=payload,
            output=output,
            tui=tui,
        )
    )
    if tui:
        try:
            await tmux_run(
                output,
                future,
                json.loads((root / "assets/public/requests.json").read_text())["requests"][0],
            )
        except Exception as exc:
            save(output / "driver-error.json", {"error_type": type(exc).__name__})
    result = await future
    print(
        json.dumps(
            {
                "termination": result.termination,
                "acceptance": result.acceptance,
                "cleanup": result.cleanup,
                "scope": result.scope,
                "strict_success": result.strict_success,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--usage", type=Path)
    parser.add_argument("--run", default="auto-1")
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args()
    if args.setup:
        setup(args.root, args.usage)
    else:
        asyncio.run(run(args.root, args.run, args.tui))
