"""逐次审计原归档、账本及补丁可应用性；原评分和派生检查分开保存。"""

from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from evaluation.formal.preserve_history import HISTORY, REPO, sha_file, stream_digest
from evaluation.formal.recover_history import docker
from evaluation.formal.run import save as create
from novacode.evaluation.contracts import EvaluationRun
from novacode.evaluation.ledger import read_records, rebuild


def save(path: Path, data: object) -> None:
    if path.exists():
        assert json.loads(path.read_text()) == data, "派生审计身份或内容变化，拒绝覆写"
    else:
        create(path, data)


def verify_original_archive(original: Path, receipt: dict[str, Any]) -> None:
    archive_path = Path(receipt["path"])
    assert sha_file(archive_path) == receipt["sha256"]
    with tarfile.open(archive_path) as archive:
        assert set(archive.getnames()) == set(receipt["files"])
        for name, expected in receipt["files"].items():
            stream = archive.extractfile(name)
            assert stream is not None and stream_digest(stream) == expected
            if (original / name).exists():
                assert sha_file(original / name) == expected
            else:
                assert name.startswith("workspace/.novacode/"), name


def audit(output: Path) -> None:
    manifest_path = REPO / "evaluation/frozen/campaign-v2/manifest.json"
    assert (
        sha_file(manifest_path)
        == "cf7191aeef652b2658456572fb737c17791649e65870ebba48812d1dd35de658"
    )
    manifest = json.loads(manifest_path.read_text())
    protocol = output.parent / "protocol-revision-20261016/before" / manifest["protocol_path"]
    assert sha_file(protocol) == manifest["protocol_sha256"]
    assert all(sha_file(REPO / p) == sha for p, sha in manifest["source_files"].items())
    assert all(sha_file(Path(p)) == sha for p, sha in manifest["external_files"].items())
    save(
        output / "historical-identity.json",
        {
            "manifest_sha256": sha_file(manifest_path),
            "explicit_protocol_mapping": {
                manifest["protocol_path"]: str(protocol.relative_to(REPO))
            },
            "protocol_sha256": sha_file(protocol),
            "source_files_verified": manifest["source_files"],
            "external_assets_verified": manifest["external_files"],
            "model_calls": 0,
            "not_executable_authorization": True,
        },
    )
    folder = output / "runs-v2"
    folder.mkdir(exist_ok=True)
    originals = sorted(
        p
        for p in (HISTORY / "runs").iterdir()
        if (p / "result.json").exists()
        and json.loads((p / "result.json").read_text())["termination"] is not None
    )
    assert len(originals) == 147
    containers = {}
    rows = []
    try:
        for original in originals:
            result = json.loads((original / "result.json").read_text())
            receipt = json.loads((original / "archive.json").read_text())
            verify_original_archive(original, receipt)
            ledgers = {}
            for ledger in (
                original / "orchestration.jsonl",
                original / "worker/ledger.jsonl",
                original / "recovery.jsonl",
            ):
                if ledger.exists():
                    records, truncated = read_records(ledger)
                    ledgers[str(ledger.relative_to(original))] = {
                        "sha256": sha_file(ledger),
                        "records": len(records),
                        "truncated": truncated,
                    }
            for artifact in (original / "worker/artifacts").glob("*.txt"):
                assert sha_file(artifact) == artifact.stem
            metrics = rebuild(original / "worker/ledger.jsonl")
            task_id = result["task_id"]
            derived = {
                "kind": "independent-patch-application-check",
                "target_tests_rerun": False,
                "result": None,
            }
            patch = original / "submission.diff"
            if patch.exists():
                if task_id.startswith("FL") and patch.stat().st_size == 0:
                    derived.update(kind="valid-empty-submission", result="passed", exit_code=0)
                    classification = "original-patch-applies"
                elif task_id.startswith("FL"):
                    if task_id not in containers:
                        environment = json.loads(
                            (
                                REPO
                                / "evaluation/frozen/live-assets/public"
                                / task_id
                                / "environment.json"
                            ).read_text()
                        )
                        name = "novacode-history-check-" + uuid4().hex
                        docker(
                            "create",
                            "--name",
                            name,
                            "--network",
                            "none",
                            "--cpus",
                            "2",
                            "--memory",
                            "4g",
                            "--pids-limit",
                            "256",
                            "--entrypoint",
                            "tail",
                            environment["image"],
                            "-f",
                            "/dev/null",
                        )
                        docker("start", name)
                        containers[task_id] = name
                    name = containers[task_id]
                    docker("cp", str(patch), name + ":/tmp/history-submission.diff")
                    check = subprocess.run(
                        [
                            "/snap/bin/docker",
                            "exec",
                            "-w",
                            "/testbed",
                            name,
                            "git",
                            "-c",
                            "core.hooksPath=/dev/null",
                            "apply",
                            "--check",
                            "--binary",
                            "/tmp/history-submission.diff",
                        ],
                        capture_output=True,
                        timeout=60,
                    )
                    derived.update(
                        result="passed" if check.returncode == 0 else "failed",
                        exit_code=check.returncode,
                        stderr=check.stderr.decode(errors="replace")[:6000],
                    )
                    classification = (
                        "original-patch-applies"
                        if check.returncode == 0
                        else "initial-baseline-conflict"
                    )
                else:
                    # 专项有固定 initial_files，原独立判题及档案均经过字节校验。
                    derived.update(
                        kind="original-specialty-judge-artifact-verification",
                        result="artifact-verified",
                    )
                    classification = "original-specialty-artifact-verified"
                derived["patch_sha256"] = sha_file(patch)
            else:
                classification = "missing-original-patch"
                derived["result"] = "unavailable"
            row = {
                "run_id": result["run_id"],
                "campaign_id": result["campaign_id"],
                "original": {
                    "result": result,
                    "strict_success": EvaluationRun.from_json(json.dumps(result)).strict_success,
                    "result_sha256": sha_file(original / "result.json"),
                    "judge": json.loads((original / "judge.json").read_text())
                    if (original / "judge.json").exists()
                    else None,
                },
                "archive": {"sha256": receipt["sha256"], "members_verified": len(receipt["files"])},
                "ledgers": ledgers,
                "cost": metrics,
                "classification": classification,
                "derived": derived,
                "historical_final_tree_verifiable": (original / "workspace").exists(),
                "limitations": [
                    "原初始清单没有完整类型/模式，已丢弃的最终业务树无法据现存归档证明无遗漏忽略文件删除",
                    "字节/应用核验不将原评分改判，不混入 v3",
                ],
                "supplemental_patch_sha256": sha_file(original / "recovery-submission.diff")
                if (original / "recovery-submission.diff").exists()
                else None,
            }
            save(folder / (original.name + ".json"), row)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "run_id": original.name,
                        "classification": classification,
                        "unknown_usage": metrics["unknown_usage_requests"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        for name in containers.values():
            docker("stop", "-t", "1", name)
            docker("rm", name)
    summary = {
        "schema_version": 1,
        "state": "HISTORICAL_ARTIFACTS_AUDITED",
        "campaign_id": manifest["campaign_id"],
        "rows": len(rows),
        "classifications": dict(Counter(r["classification"] for r in rows)),
        "original_strict_successes": sum(r["original"]["strict_success"] for r in rows),
        "known_token_lower_bound": sum(r["cost"]["known_token_lower_bound"] for r in rows),
        "unknown_usage_requests": sum(r["cost"]["unknown_usage_requests"] for r in rows),
        "model_calls": 0,
        "v3_results": 0,
        "missing_primary_time": [
            r["run_id"] for r in rows if r["cost"]["main_end_to_end_seconds"] is None
        ],
        "all_original_scores_unchanged": True,
    }
    save(output / "history-summary-v2.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.output.resolve())
