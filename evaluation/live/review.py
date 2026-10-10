"""从入库账本复核全部公开/隐藏指纹，按原字节导出资格证据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from collections import Counter
from pathlib import Path

from novacode.evaluation.assets import validate_task_assets
from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.corpus import SelectionLedger


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(assets: Path, root: Path, output: Path) -> None:
    tasks = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted((assets / "contracts").glob("*.json"))
    )
    public, hidden = assets / "public", assets / "external"
    ledger = SelectionLedger(
        assets / "selection.jsonl",
        tasks,
        public,
        hidden,
        frozenset(t.budget_category for t in tasks),
    )
    states, _ = ledger.replay()
    assert len(tasks) == 12 and set(states.values()) == {"ADMITTED"}
    assert Counter(t.difficulty for t in tasks) == {"easy": 4, "medium": 6, "hard": 2}
    assert max(Counter(t.repository for t in tasks).values()) <= 2
    output.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        (request,) = validate_task_assets(task, public, hidden, ledger.budget_categories)
        check = json.loads((hidden / f"{task.task_id}/qualification-checks.json").read_text())
        source = Path(check["raw_evidence"])
        assert source.resolve().is_relative_to((root / "final-qualification").resolve())
        row = json.loads((source / "row.json").read_text())
        assert request == row["problem_statement"]
        assert set(json.loads((public / task.public_input.path).read_text())) == {
            "schema_version",
            "requests",
        }
        for name, sha in check["raw_fingerprints"].items():
            assert digest(source / name) == sha
        result = check["qualification"]
        assert result["qualified"] and result["model_calls"] == 0
        assert all(r["seconds"] <= 600 for r in result["runs"])
        assert [r["label"] for r in result["runs"]] == [
            "original",
            "reference-1",
            "reference-2",
            "reference-3",
        ]
        assert [r["official"]["resolved"] for r in result["runs"]] == [False, True, True, True]
        assert all(
            not r["official"]["tests_status"]["PASS_TO_PASS"]["failure"] for r in result["runs"]
        )
        assert (source / "reference-3/target-before.txt").read_bytes() == (
            source / "reference-3/target-after.txt"
        ).read_bytes()
        for label in ("original", "reference-1", "reference-2", "reference-3"):
            actual = json.loads((source / label / "container.json").read_text())["HostConfig"]
            assert actual["NetworkMode"] == "none"
            assert (actual["NanoCpus"], actual["Memory"], actual["PidsLimit"]) == (
                2_000_000_000,
                4294967296,
                256,
            )
        with tarfile.open(output / (task.task_id + "-qualification.tar.gz"), "x:gz") as archive:
            archive.add(source, arcname=task.candidate_id)
    pool = root / "pool-final"
    for name in ("dataset-identity.json", "selection-rule.json", "pool.json", "pool-summary.json"):
        shutil.copy2(pool / name, output / name)
    for name in (
        "selected.json",
        "selection-decisions.json",
        "additional-complexity-reviews.json",
        "runtime.json",
        "transport-policy.json",
    ):
        if (root / name).exists():
            shutil.copy2(root / name, output / name)
    shutil.copy2(assets / "selection.jsonl", output / "selection.jsonl")
    shutil.copy2(assets / "admitted.json", output / "admitted.json")
    print("12 Live admissions and raw fingerprints verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review(args.assets, args.root, args.output)
