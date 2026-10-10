"""按事前顺序核验和入库；只消费离线资格结果，不发起模型调用。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from novacode.evaluation.contracts import Asset, EvaluationTask, fingerprint, json_text
from novacode.evaluation.corpus import Qualification, SelectionLedger, quota_report, validate_corpus
from novacode.evaluation.live import candidate_order, public_package


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset(root: Path, name: str, value: object) -> Asset:
    path = root / name
    save(path, value)
    return Asset(name, digest(path))


def qualification_base(root: Path, identity: str) -> Path:
    """准备通道更新后继续消费首个实际验收记录，不重跑已有验收。"""
    policy = root / "transport-policy.json"
    names = json.loads(policy.read_text())["reused_test_attempts"] if policy.exists() else []
    chosen = next(
        (n for n in names if n == identity or n.startswith(identity + "-image-retry-")), identity
    )
    return root / "final-qualification" / chosen


def qualify_candidate(
    entry: dict[str, Any], root: Path, pool: Path, archive: Path, grader: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = root.parent / "stage1/verified.parquet"
    base = qualification_base(root, entry["instance_id"])
    transport = root / "transport-policy.json"
    extra = (
        ["--image-transport", json.loads(transport.read_text())["primary_transport"]]
        if transport.exists()
        else []
    )
    retries = []
    for attempt in range(1, 4):
        output = base if attempt == 1 else base.with_name(base.name + f"-image-retry-{attempt}")
        if not output.exists():
            subprocess.run(
                [
                    str(grader),
                    str(Path(__file__).with_name("qualify.py")),
                    "--dataset",
                    str(dataset),
                    "--instance",
                    entry["instance_id"],
                    "--output",
                    str(output),
                    "--archive",
                    str(archive),
                    "--rule",
                    str(pool / "selection-rule.json"),
                    *extra,
                ],
                check=True,
            )
        result = json.loads((output / "qualification.json").read_text())
        pull_log = output / "image-pull.txt"
        if (
            not (output / "original").exists()
            and pull_log.exists()
            and "http error 429" in pull_log.read_text().lower()
        ):
            raise RuntimeError(
                f"Registry 拉取额度耗尽，暂停选择而非排除候选；准备记录保留于 {output}"
            )
        if not image_retryable(result, output) or attempt == 3:
            break
        retries.append(
            {
                "path": str(output.relative_to(root)),
                "qualification_sha256": digest(output / "qualification.json"),
            }
        )
    assert result["runtime_sha256"] == digest(archive), "不能复用其他运行时的资格记录"
    event = {
        "schema_version": 1,
        "instance_id": entry["instance_id"],
        "difficulty": entry["difficulty"],
        "qualification_sha256": digest(output / "qualification.json"),
        "action": "QUALIFIED" if result["qualified"] else "EXCLUDED",
        "reason": result.get("error", "original-failure-and-three-reference-passes"),
        "qualification_relative_path": str(output.relative_to(root)),
        "preparation_retries": retries,
    }
    event_path = root / "attempts" / (output.name + ".json")
    if not event_path.exists():
        save(event_path, event)
    return result, event


def image_retryable(result: dict[str, Any], output: Path) -> bool:
    """只恢复首次测试前的下载中断，不重试原始或参考验收。"""
    if result["qualified"] or (output / "original").exists():
        return False
    log = output / "image-pull.txt"
    return log.exists() and (
        "TimeoutExpired" in result.get("error", "")
        or result.get("error", "").startswith("PreparationInterrupted:")
        or any(
            word in log.read_text().lower()
            for word in (
                "unexpected eof",
                "unexpected_eof_while_reading",
                ": eof",
                "timed out",
                "handshake timeout",
                "i/o timeout",
                "http error 401",
                "registry blob 大小不符",
            )
        )
    )


def select(pool: Path, archive: Path, grader: Path) -> None:
    rule = json.loads((pool / "selection-rule.json").read_text())
    entries = json.loads((pool / "pool.json").read_text())["rows"]
    root = pool.parent
    accepted: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    keys: set[str] = set()
    attempted = 0
    decisions: list[dict[str, Any]] = []
    review_path = root / "additional-complexity-reviews.json"
    additional_reviews = json.loads(review_path.read_text()) if review_path.exists() else {}
    for level in rule["stratum_order"]:
        for entry in candidate_order(entries, level):
            if counts[entry["repository"]] >= rule["repository_limit"]:
                decisions.append(
                    {"instance_id": entry["instance_id"], "reason": "repository-limit"}
                )
                continue
            if keys & set(entry["problem_keys"]):
                decisions.append({"instance_id": entry["instance_id"], "reason": "family-conflict"})
                continue
            if entry["instance_id"] in additional_reviews:
                review = additional_reviews[entry["instance_id"]]
                assert review["difficulty"] == level, "补充审查不能静默改动事前层或顺序"
                entry = {**entry, "difficulty_status": "reviewed", "complexity_review": review}
                if review.get("exclude_reason"):
                    decisions.append(
                        {"instance_id": entry["instance_id"], "reason": review["exclude_reason"]}
                    )
                    continue
            if entry["difficulty_status"] != "reviewed":
                raise ValueError("需要在判题前保存认知跨度审查: " + entry["instance_id"])
            attempted += 1
            assert attempted <= rule["maximum_candidates"], "达到事前资格尝试上限"
            result, event = qualify_candidate(entry, root, pool, archive, grader)
            decisions.append(event)
            if result["qualified"]:
                accepted.append(
                    {**entry, "qualification_relative_path": event["qualification_relative_path"]}
                )
                counts[entry["repository"]] += 1
                keys.update(entry["problem_keys"])
            if sum(e["difficulty"] == level for e in accepted) == rule["difficulty_targets"][level]:
                break
        assert (
            sum(e["difficulty"] == level for e in accepted) == rule["difficulty_targets"][level]
        ), "资格池缺额"
    save(root / "selected.json", {"schema_version": 1, "entries": accepted})
    save(root / "selection-decisions.json", {"schema_version": 1, "decisions": decisions})
    print("12 QUALIFIED", flush=True)


def build(pool: Path, output: Path, grader: Path) -> None:
    rule = json.loads((pool / "selection-rule.json").read_text())
    root = pool.parent
    entries = json.loads((root / "selected.json").read_text())["entries"]
    entries.sort(key=lambda e: (["easy", "medium", "hard"].index(e["difficulty"]), e["rank"]))
    output.mkdir(parents=True, exist_ok=False)
    public, hidden = output / "public", output / "external"
    public.mkdir()
    hidden.mkdir()
    task_list: list[EvaluationTask] = []
    proofs = {}
    for index, entry in enumerate(entries, 1):
        task_id = f"L{index:02}"
        qualification_root = root / entry["qualification_relative_path"]
        assert qualification_root.resolve().is_relative_to((root / "final-qualification").resolve())
        row = json.loads((qualification_root / "row.json").read_text())
        result = json.loads((qualification_root / "qualification.json").read_text())
        assert result["qualified"]
        initial = asset(
            public,
            f"{task_id}/initial.json",
            {"schema_version": 1, "base_commit": row["base_commit"]},
        )
        request = asset(public, f"{task_id}/requests.json", public_package(row))
        env = asset(
            public,
            f"{task_id}/environment.json",
            {
                "schema_version": 1,
                "image": result["image"],
                "resources": {k: rule["limits"][k] for k in ("cpus", "memory_bytes", "pids")},
            },
        )
        row_asset = asset(hidden, f"{task_id}/row.json", row)
        judge_path = hidden / f"{task_id}/judge.py"
        judge_path.write_bytes(Path(__file__).with_name("judge.py").read_bytes())
        judge_asset = Asset(f"{task_id}/judge.py", digest(judge_path))
        acceptance = asset(
            hidden,
            f"{task_id}/acceptance.json",
            {
                "schema_version": 1,
                "image": result["image"],
                "limits": rule["limits"],
                "row_sha256": row_asset.sha256,
                "judge_sha256": judge_asset.sha256,
                "grader_commit": rule["grader_commit"],
                "conditions": ["official-tests"],
                "timeout_seconds": rule["limits"]["test_timeout_seconds"],
                "command": [
                    str(grader),
                    str(judge_path.resolve()),
                    str((hidden / f"{task_id}/acceptance.json").resolve()),
                ],
            },
        )
        task = EvaluationTask(
            task_id=task_id,
            candidate_id=entry["instance_id"],
            source="live",
            suite="development",
            family_id="live:" + entry["problem_keys"][0],
            problem_keys=tuple(entry["problem_keys"]),
            repository=row["repo"],
            source_revision=rule["dataset_revision"],
            base_commit=row["base_commit"],
            difficulty=entry["difficulty"],
            category="issue-fix",
            pressure_tags=(),
            budget_category="live-" + entry["difficulty"],
            initial_state=initial,
            public_input=request,
            environment=env,
            acceptance_assets=(acceptance, row_asset, judge_asset),
            acceptance_conditions=("official-tests",),
            allowed_paths=("**",),
            builder_id="live-curator-v1",
        )
        contract = output / "contracts" / (task_id + ".json")
        contract.parent.mkdir(exist_ok=True)
        contract.write_text(json_text(task) + "\n")
        evidence = asset(
            hidden,
            f"{task_id}/qualification-checks.json",
            {
                "schema_version": 1,
                "qualification": result,
                "complexity_review": entry["complexity_review"],
                "raw_evidence": str(qualification_root),
                "raw_fingerprints": {
                    p.relative_to(qualification_root).as_posix(): digest(p)
                    for p in qualification_root.rglob("*")
                    if p.is_file()
                },
            },
        )
        qualification = Qualification(
            task_sha256=fingerprint(task),
            environment_verified=True,
            resources_verified=True,
            original_target_failed=True,
            original_regressions_passed=True,
            reference_passes=(True, True, True),
            correct_implementation_passed=False,
            negative_controls_passed=False,
            evidence=(evidence,),
        )
        proof = hidden / f"{task_id}/qualification.json"
        proof.write_text(json_text(qualification) + "\n")
        proofs[task_id] = Asset(f"{task_id}/qualification.json", digest(proof))
        task_list.append(task)
    tasks = tuple(task_list)
    ledger = SelectionLedger(
        output / "selection.jsonl",
        tasks,
        public,
        hidden,
        frozenset(t.budget_category for t in tasks),
    )
    for task in tasks:
        ledger.append(
            task.task_id, "SELECT", "selection-rule", "完整池固定种子、认知审查与同层替补"
        )
        ledger.append(
            task.task_id,
            "ADMIT",
            "qualification-passed",
            "离线原始失败、三次参考及独立 Agent 环境",
            evidence=proofs[task.task_id],
        )
    states, _ = ledger.replay()
    specialties = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted(Path("evaluation/specialty/assets/contracts").glob("*.json"))
    )
    specialty_ledger = SelectionLedger(
        Path(
            "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-8/specialty-admission/selection.jsonl"
        ),
        specialties,
        Path("evaluation/specialty/assets/public"),
        Path("evaluation/specialty/assets/external"),
        frozenset(t.budget_category for t in specialties),
    )
    specialty_states, _ = specialty_ledger.replay()
    validate_corpus((*tasks, *specialties))
    quota = quota_report((*tasks, *specialties), {**states, **specialty_states})
    assert all(r["gap"] == 0 for r in quota["rows"] if r["suite"] == "development")
    save(
        output / "admitted.json",
        {
            "schema_version": 1,
            "state": "DEV_ADMITTED",
            "quota": quota,
            "live_ids": {t.task_id: t.candidate_id for t in tasks},
            "model_calls": 0,
        },
    )
    print("DEV_ADMITTED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("select", "build"))
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--grader",
        type=Path,
        default=Path.home() / ".cache/novacode-evaluation/stage1/grader-venv/bin/python",
    )
    args = parser.parse_args()
    if args.action == "select":
        select(args.pool, args.archive, args.grader)
    else:
        build(args.pool, args.output, args.grader)
