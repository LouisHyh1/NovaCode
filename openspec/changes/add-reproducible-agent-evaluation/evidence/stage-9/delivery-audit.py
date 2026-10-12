"""只读复核第九阶段合同、原件、账本、产物、跨轮预算与凭据。"""

import argparse
import base64
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path

from novacode.config import load
from novacode.evaluation.assets import validate_task_assets
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.corpus import SelectionLedger, validate_corpus
from novacode.evaluation.ledger import rebuild

repo = Path.cwd()
ev = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
tasks: list[EvaluationTask] = []
for source in ("live", "specialty"):
    assets = repo / "evaluation" / source / "assets"
    cohort = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted((assets / "contracts").glob("*.json"))
    )
    ledger_path = (
        assets / "selection.jsonl"
        if source == "live"
        else ev / "specialty-admission/selection.jsonl"
    )
    categories = frozenset(t.budget_category for t in cohort)
    state, _ = SelectionLedger(
        ledger_path, cohort, assets / "public", assets / "external", categories
    ).replay()
    assert len(cohort) == 12 and set(state.values()) == {"ADMITTED"}
    for task in cohort:
        validate_task_assets(task, assets / "public", assets / "external", categories)
    tasks.extend(cohort)
validate_corpus(tuple(tasks))
budget = json.loads((ev / "calibrated-budgets.json").read_text())
BudgetLimits(**budget["total"])
for item in budget["categories"].values():
    BudgetLimits(**item["limits"])
shared = json.loads((ev / "shared-budget-audit.json").read_text())
assert all(shared["reserved_sums"][f] <= shared["total"][f] for f in shared["reserved_sums"])
secrets = [
    p.api_key.encode() for p in load(str(repo / ".novacode/config.yaml")).providers if p.api_key
]
auth_path = Path.home() / "snap/docker/current/.docker/config.json"
if auth_path.exists():
    for auth in json.loads(auth_path.read_text()).get("auths", {}).values():
        if auth.get("auth"):
            secrets += [auth["auth"].encode(), base64.b64decode(auth["auth"]).partition(b":")[2]]
archives = 0
unknown = 0
for round_name, expected in (("measurement-v1", 75), ("calibration-v2", 6)):
    folder = ev / round_name
    manifest = json.loads((folder / "archive-manifest.json").read_text())["files"]
    assert len(manifest) == expected
    for name, digest in manifest.items():
        path = folder / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        with tarfile.open(path) as archive:
            members = {}
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    members[member.name] = stream.read()
        assert not any(s and s in raw for raw in members.values() for s in secrets)
        with tempfile.TemporaryDirectory(prefix="stage9-ledger-") as directory:
            ledger = Path(directory) / "ledger.jsonl"
            ledger.write_bytes(members["worker/ledger.jsonl"])
            metric = rebuild(ledger)
        assert (
            not metric["truncated_tail"]
            and metric["incomplete_tools"] == metric["incomplete_phases"] == 0
        )
        unknown += metric["unknown_usage_requests"]
        for name_in_tar, raw in members.items():
            if "/artifacts/" in name_in_tar and name_in_tar.endswith(".txt"):
                assert Path(name_in_tar).stem == hashlib.sha256(raw).hexdigest()
        archives += 1
assert unknown == 1
for path in ev.rglob("*"):
    if path.is_file() and "__pycache__" not in path.parts:
        assert not any(s and s in path.read_bytes() for s in secrets), str(path)
result = {
    "schema_version": 1,
    "admitted_development_tasks": 24,
    "main_runs": 72,
    "tmux_runs": 3,
    "calibration_runs": 6,
    "archives": archives,
    "unknown_usage_requests_retained": unknown,
    "secrets_absent": True,
    "ledger_and_artifact_hashes_valid": True,
    "shared_budget_valid": True,
    "formal_model_calls": 0,
    "state": "PILOT_COMPLETE",
}
print(json.dumps(result, ensure_ascii=False))
if args.output:
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
