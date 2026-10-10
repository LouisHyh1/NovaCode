"""核对本轮入库、资产与交付归档中的本机凭据；不输出凭据值。"""

import argparse
import base64
import json
import tarfile
from pathlib import Path

from novacode.config import load
from novacode.evaluation.assets import validate_task_assets
from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.corpus import SelectionLedger, quota_report, validate_corpus

root = Path.cwd()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, help="可选的新审计文件；默认只读复核")
args = parser.parse_args()
ev = root / "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-8"
tasks = []
states = {}
for source in ("live", "specialty"):
    assets = root / "evaluation" / source / "assets"
    cohort = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted((assets / "contracts").glob("*.json"))
    )
    ledger_path = (
        assets / "selection.jsonl"
        if source == "live"
        else ev / "specialty-admission/selection.jsonl"
    )
    ledger = SelectionLedger(
        ledger_path,
        cohort,
        assets / "public",
        assets / "external",
        frozenset(t.budget_category for t in cohort),
    )
    current, _ = ledger.replay()
    assert len(cohort) == 12 and set(current.values()) == {"ADMITTED"}
    for task in cohort:
        validate_task_assets(task, assets / "public", assets / "external", ledger.budget_categories)
    tasks.extend(cohort)
    states.update(current)
validate_corpus(tuple(tasks))
quota = quota_report(tuple(tasks), states)
assert all(
    r["gap"] == 0 and all(x == 0 for x in r["difficulty_gaps"].values())
    for r in quota["rows"]
    if r["suite"] == "development"
)
assert (
    json.loads((root / "evaluation/live/assets/admitted.json").read_text())["state"]
    == "DEV_ADMITTED"
)
secrets = [
    p.api_key.encode() for p in load(str(root / ".novacode/config.yaml")).providers if p.api_key
]
cfg = json.loads((Path.home() / "snap/docker/current/.docker/config.json").read_text())
for v in cfg["auths"].values():
    if v.get("auth"):
        secrets.append(v["auth"].encode())
        credential = base64.b64decode(v["auth"]).partition(b":")[2]
        if credential:
            secrets.append(credential)
files = 0
members = 0
paths = list((root / "evaluation/live").rglob("*")) + list(ev.rglob("*"))
for path in paths:
    if not path.is_file() or "__pycache__" in path.parts:
        continue
    content = path.read_bytes()
    assert not any(s in content for s in secrets), str(path)
    files += 1
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                content = stream.read()
                assert not any(s in content for s in secrets), str(path) + "!" + member.name
                members += 1
result = {
    "schema_version": 1,
    "state": "DEV_ADMITTED",
    "quota": quota,
    "tasks": len(tasks),
    "credential_matches": 0,
    "scanned_files": files,
    "scanned_archive_members": members,
    "boundary": "12 Live + 12 specialty admission only; no pilot/frozen/formal success claim",
}
if args.output is not None:
    with args.output.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
print("24 admissions, quotas, families, assets and credential scan verified", files, members)
