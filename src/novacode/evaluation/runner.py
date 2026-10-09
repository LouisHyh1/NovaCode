"""外围容器编排：无主机挂载，停止、提取后才允许独立判题。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from novacode.evaluation.artifacts import RUNTIME_EXCLUSIONS, extract_patch, scope_status
from novacode.evaluation.assets import validate_task_assets, verify_asset
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.contracts import (
    EvaluationRun,
    EvaluationTask,
    fingerprint,
    json_text,
    require,
)
from novacode.evaluation.ledger import Ledger


def validate_execution(payload: dict[str, Any], task: EvaluationTask, hidden: Path) -> None:
    require(type(payload.get("context_compression", True)) is bool, "压缩策略必须为布尔值")
    cfg = payload["provider"]
    require(
        cfg["protocol"] == "anthropic"
        and cfg["model"] == "deepseek-v4-flash"
        and cfg["thinking"] is True,
        "代表题模型协议不一致",
    )
    require(
        type(cfg.get("max_retries")) is int and cfg["max_retries"] == 0, "必须禁用 SDK 自动重试"
    )
    require(
        type(cfg.get("timeout")) in (int, float)
        and math.isfinite(cfg["timeout"])
        and cfg["timeout"] > 0,
        "缺少有限请求超时",
    )
    require(
        type(cfg.get("max_output_tokens")) is int and cfg["max_output_tokens"] > 0, "缺少输出上限"
    )
    allowed = payload["allowed_tools"]
    require(
        isinstance(allowed, list)
        and bool(allowed)
        and len(allowed) == len(set(allowed))
        and set(allowed) <= {"read_file", "write_file", "edit_file", "bash", "glob", "grep"},
        "工具允许集合无效",
    )
    from novacode.permission.settings import PermissionsBlock, Settings, to_rule_set

    to_rule_set(Settings(permissions=PermissionsBlock(**payload["permissions"])))
    manifest = json.loads(verify_asset(task.acceptance_assets[0], hidden).read_text())
    require(
        isinstance(manifest["command"], list)
        and bool(manifest["command"])
        and all(isinstance(a, str) and a for a in manifest["command"]),
        "判题命令缺失",
    )
    require(
        type(manifest["timeout_seconds"]) in (int, float)
        and math.isfinite(manifest["timeout_seconds"])
        and manifest["timeout_seconds"] > 0,
        "判题时间不完整",
    )


async def command(*args: str, stdin: bytes | None = None, timeout: float = 120) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        # stderr 可能包含请求秘密；账本仅保存异常类型。
        raise RuntimeError(
            f"外部命令失败: {args[0]} {args[1] if len(args) > 1 else ''}, exit={proc.returncode}"
        )
    return stdout


def grade_result(run: EvaluationRun, task: EvaluationTask, report: dict[str, Any]) -> EvaluationRun:
    conditions = report.get("conditions", {})
    require(isinstance(conditions, dict), "无效判题报告")
    require(set(conditions) == set(task.acceptance_conditions), "独立验收项缺失或新增")
    require(all(type(value) is bool for value in conditions.values()), "验收项必须为布尔值")
    resolved = report.get("live_resolved")
    require(task.source != "live" or type(resolved) is bool, "Live 缺少独立 resolved")
    return replace(
        run,
        state="RECORDED",
        acceptance="passed" if all(conditions.values()) else "failed",
        live_resolved=resolved if task.source == "live" else None,
    )


async def run_task(
    task: EvaluationTask,
    *,
    public_root: Path,
    acceptance_root: Path,
    runtime_archive: Path,
    payload: dict[str, Any],
    output: Path,
    tui: bool = False,
) -> EvaluationRun:
    output.mkdir(parents=True, exist_ok=False)
    ledger = Ledger(
        output / "orchestration.jsonl", payload["run_id"], secrets=(payload["provider"]["api_key"],)
    )
    run = EvaluationRun(
        run_id=payload["run_id"],
        campaign_id=payload["campaign_id"],
        task_id=task.task_id,
        task_sha256=fingerprint(task),
        initial_sha256=task.initial_state.sha256,
        config_id=payload["config_id"],
        repeat_id=payload["repeat_id"],
        state="PREFLIGHT",
    )
    container = "novacode-eval-" + uuid4().hex
    created = False
    started = False
    stopped = False
    initial_ignored: dict[str, str] = {}
    try:
        with ledger.phase("preparation"):
            requests = validate_task_assets(
                task, public_root, acceptance_root, frozenset({task.budget_category})
            )
            require(task.suite == "development", "冻结执行尚未开放")
            limits = BudgetLimits(**payload["limits"])
            BudgetLimits(**payload["total_limits"])
            validate_execution(payload, task, acceptance_root)
            require(payload["config_id"] == "eager-schema", "尚未实现其他策略")
            cfg = payload["provider"]
            require(
                cfg.get("max_retries") == 0
                and cfg.get("timeout", 0) > 0
                and cfg.get("max_output_tokens", 0) > 0,
                "请求控制不完整",
            )
            environment = json.loads(verify_asset(task.environment, public_root).read_text())
            initial = json.loads(verify_asset(task.initial_state, public_root).read_text())
            require(
                initial == {"schema_version": 1, "base_commit": task.base_commit},
                "初始环境合同不一致",
            )
            image = environment["image"]
            require(
                re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image) is not None,
                "镜像必须固定 digest",
            )
            resources = environment["resources"]
            require(
                all(
                    type(resources[k]) is int and resources[k] > 0
                    for k in ("cpus", "memory_bytes", "pids")
                ),
                "缺少资源上限",
            )
            archive_asset = environment["runtime"]
            with runtime_archive.open("rb") as archive:
                require(
                    hashlib.file_digest(archive, "sha256").hexdigest() == archive_asset["sha256"],
                    "运行环境指纹不符",
                )
            await command("docker", "info", "--format", "{{.ServerVersion}}")
            await command("docker", "image", "inspect", image)
            await command(
                "docker",
                "create",
                "--name",
                container,
                "--cpus",
                str(resources["cpus"]),
                "--memory",
                str(resources["memory_bytes"]),
                "--memory-swap",
                str(resources["memory_bytes"]),
                "--pids-limit",
                str(resources["pids"]),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                image,
                "tail",
                "-f",
                "/dev/null",
            )
            created = True
            await command("docker", "start", container)
            await command("docker", "cp", str(runtime_archive), container + ":/agent-runtime.tar")
            await command(
                "docker",
                "exec",
                container,
                "tar",
                "--no-same-owner",
                "-xf",
                "/agent-runtime.tar",
                "-C",
                "/",
            )
            await command(
                "docker",
                "exec",
                container,
                "bash",
                "-lc",
                "/opt/novacode/uv venv /opt/novacode/venv "
                "--python /opt/novacode/python/bin/python3.12 && "
                "/opt/novacode/uv pip install --offline --no-index "
                "--find-links /opt/novacode/wheels "
                "--python /opt/novacode/venv/bin/python novacode",
            )
            actual = await command(
                "docker", "exec", "-w", "/testbed", container, "git", "rev-parse", "HEAD"
            )
            require(actual.decode().strip() == task.base_commit, "任务源码不是固定 base")
            dirty = await command(
                "docker", "exec", "-w", "/testbed", container, "git", "status", "--porcelain"
            )
            require(not dirty, "任务初始源码不干净")
            ignored = await command(
                "docker",
                "exec",
                "-w",
                "/testbed",
                container,
                "python",
                "-c",
                "import subprocess,hashlib,json; from pathlib import Path; "
                "names=subprocess.check_output(['git','ls-files','--others','-i',"
                "'--exclude-standard','-z']).decode().split(chr(0)); "
                "print(json.dumps({n:hashlib.sha256(Path(n).read_bytes()).hexdigest() "
                "for n in names if n and Path(n).is_file() and not Path(n).is_symlink()}))",
            )
            initial_ignored = json.loads(ignored)
            await command("docker", "exec", container, "mkdir", "-p", "/isolated-user")
            worker_payload = {**payload, "requests": list(requests)}
            if tui:
                await command(
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "sh",
                    "-c",
                    "cat > /dev/shm/novacode-input",
                    stdin=json.dumps(worker_payload).encode(),
                )
            ledger.append(
                "preflight_passed",
                task_sha256=fingerprint(task),
                environment=environment,
                runtime_exclusions=RUNTIME_EXCLUSIONS,
                initial_ignored=initial_ignored,
            )
        reserve_run(
            output.parent,
            payload["campaign_id"],
            payload["run_id"],
            limits,
            BudgetLimits(**payload["total_limits"]),
        )
        started = True
        with ledger.phase("initialization"):
            run = replace(run, state="RUNNING")
            ledger.append("run_started")
        with ledger.phase("agent"):
            args = [
                "docker",
                "exec",
                "-i",
                "-w",
                "/testbed",
                "-e",
                "HOME=/isolated-user",
                container,
                "/opt/novacode/venv/bin/python",
                "-m",
                "novacode.evaluation.worker",
            ]
            if tui:
                # tmux pane 的完整命令只含容器 ID，不含凭证。
                terminal = output / "tui-command.json"
                terminal.write_text(
                    json.dumps(
                        [*args[:2], "-it", *args[3:], "--tui", "--input", "/dev/shm/novacode-input"]
                    )
                )
                ledger.append("tui_ready", container=container)
                async with asyncio.timeout(limits.seconds + limits.cleanup_seconds):
                    while not (output / "tui-exited").exists():
                        await asyncio.sleep(0.25)
            else:
                await command(
                    *args,
                    stdin=(json.dumps(worker_payload) + "\n").encode(),
                    timeout=limits.seconds + limits.cleanup_seconds,
                )
    except BaseException as exc:
        if not started:
            run = replace(run, block_reason=f"{type(exc).__name__}: {exc}")
            ledger.append("environment_blocked", error_type=type(exc).__name__, reason=str(exc))
        else:
            from novacode.evaluation.worker import termination

            run = replace(run, termination=termination(exc))
            ledger.append("execution_error", error_type=type(exc).__name__)
    finally:
        if created:
            stopped = await stop_and_extract(container, output, started, payload, ledger)
        if started:
            run = await finalize(
                run, task, output, acceptance_root, stopped, ledger, initial_ignored
            )
        (output / "result.json").write_text(json_text(run) + "\n")
        ledger.append("run_result", result=asdict(run), strict_success=run.strict_success)
        ledger.close()
    return run


async def stop_and_extract(
    container: str,
    output: Path,
    started: bool,
    payload: dict[str, Any],
    ledger: Ledger,
) -> bool:
    stopped = False
    with ledger.phase("cleanup"):
        try:
            await command(
                "docker", "stop", "-t", "1", container, timeout=payload["limits"]["cleanup_seconds"]
            )
            state = await command("docker", "inspect", "--format", "{{.State.Running}}", container)
            require(state.strip() == b"false", "所属容器进程仍在运行")
            stopped = True
            if started:
                await command("docker", "cp", container + ":/testbed", str(output / "workspace"))
                try:
                    await command(
                        "docker", "cp", container + ":/run/novacode-output", str(output / "worker")
                    )
                except RuntimeError:
                    pass
        except BaseException as exc:
            ledger.append("cleanup_error", error_type=type(exc).__name__)
        finally:
            try:
                await command("docker", "rm", "-f", container)
            except BaseException:
                stopped = False
    return stopped


async def finalize(
    run: EvaluationRun,
    task: EvaluationTask,
    output: Path,
    acceptance_root: Path,
    stopped: bool,
    ledger: Ledger,
    initial_ignored: dict[str, str] | None = None,
) -> EvaluationRun:
    worker = output / "worker" / "worker-result.json"
    result = json.loads(worker.read_text()) if worker.exists() else {}
    run = replace(
        run,
        state="STOPPING",
        termination=run.termination or result.get("termination", "runner-error"),
        agent_run_ids=tuple(result.get("agent_run_ids", run.agent_run_ids)),
        completed_requests=result.get("completed_requests", run.completed_requests),
        cleanup="passed" if stopped and result.get("cleanup") == "passed" else "failed",
        scope="unknown",
    )
    try:
        require(stopped, "停止失败时禁止判题")
        for asset in task.acceptance_assets:
            verify_asset(asset, acceptance_root)
        patch, names = extract_patch(output / "workspace", task.base_commit, initial_ignored)
        (output / "submission.diff").write_text(patch)
        run = replace(run, scope=scope_status(names, task.allowed_paths), state="GRADING")
        ledger.append("submission", paths=names, patch=ledger.artifact(patch))
        with ledger.phase("grading"):
            manifest = json.loads(
                verify_asset(task.acceptance_assets[0], acceptance_root).read_text()
            )
            argv = manifest["command"]
            require(
                isinstance(argv, list) and bool(argv) and all(isinstance(a, str) for a in argv),
                "无效外部判题命令",
            )
            report = await command(
                *argv,
                str((output / "submission.diff").resolve()),
                str(output.resolve()),
                timeout=manifest["timeout_seconds"],
            )
            (output / "judge.json").write_bytes(report)
            run = grade_result(run, task, json.loads(report))
    except Exception as exc:
        ledger.append("ungradable", error_type=type(exc).__name__)
        run = replace(run, state="RECORDED", acceptance="ungradable")
    return run
