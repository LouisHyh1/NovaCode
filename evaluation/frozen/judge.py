"""冻结题复用既有事实/行为判题，补测题按显式被测符号验收。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation/specialty"))

from evaluation.specialty.judge import grade as specialty_grade  # noqa: E402
from evaluation.specialty.judge import run_tests, submission, valid_tests  # noqa: E402
from novacode.evaluation.artifacts import scope_status  # noqa: E402
from novacode.evaluation.contracts import require  # noqa: E402


def grade(
    manifest: dict[str, Any], changes: dict[str, str], answer: str, *, local: bool = False
) -> dict[str, Any]:
    if not manifest["mutants"]:
        return specialty_grade(manifest, changes, answer, local=local)
    initial = manifest["initial_files"]
    names = [name for name, value in changes.items() if initial.get(name) != value]
    added = changes.get("tests/test_added.py", "")
    files = {**initial, "tests/test_added.py": added}
    correct = run_tests(files, manifest["image"], local=local)
    target = manifest["test_target"]
    correct_pass = (
        bool(added)
        and valid_tests(correct)
        and correct["successful"]
        and target in correct.get("executed", [])
    )
    mutants = {
        key: run_tests({**files, **defect}, manifest["image"], local=local)
        for key, defect in manifest["mutants"].items()
    }
    return {
        "conditions": {
            "scope": scope_status(names, tuple(manifest["allowed_paths"])) == "passed",
            "correct-tests": correct_pass,
            "all-mutants": all(
                valid_tests(r)
                and r["failures"] > 0
                and target in r.get("executed", [])
                and any(t.startswith("test_added.") for t in r.get("failure_ids", []))
                for r in mutants.values()
            ),
        },
        "details": {"correct": correct, "mutants": mutants},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("patch", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    for name, digest in manifest["implementation"].items():
        require(hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest, "判题实现变化")
    worker = json.loads((args.output / "worker/worker-result.json").read_text())
    final = args.output / "worker" / worker["final"]["path"]
    print(json.dumps(grade(manifest, submission(manifest, args.patch), final.read_text())))


if __name__ == "__main__":
    main()
