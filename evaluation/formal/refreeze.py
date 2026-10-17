"""用户确认的v2协议重冻；沿用未改动Live冻结合同，重验全部专项。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from evaluation.frozen.freeze import LEDGERS, ROOTS, build  # noqa: E402
from novacode.evaluation.contracts import require  # noqa: E402
from novacode.evaluation.frozen import digest  # noqa: E402


def main(runtime: Path) -> None:
    old = REPO / "evaluation/frozen/campaign/manifest.json"
    require(
        digest(old) == "67a35add8b42f90b2d8dc0fb842444a5b410f576f17a7e75889e2ebb70d1b32c",
        "v1历史清单变化",
    )
    evidence = REPO / "openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11"
    roots = {**ROOTS, "specialty": "evaluation/frozen/specialty-assets-v2"}
    ledgers = {
        **LEDGERS,
        "specialty": str((evidence / "specialty-readmission-v2/selection.jsonl").relative_to(REPO)),
    }
    before = json.loads(old.read_text())
    # Live完整目录与原合同逐文件完全相同，复用原冻结资格，不重写旧账本。
    for name, sha in before["corpus"]["files"].items():
        if name.startswith(ROOTS["live"] + "/"):
            require(digest(REPO / name) == sha, "未授权Live变更")
    build(
        REPO / "evaluation/frozen/campaign-v2",
        runtime,
        next((runtime.parent / "wheels").glob("novacode-*.whl")),
        campaign_id="novacode-frozen-20261015-v2",
        roots=roots,
        ledgers=ledgers,
        deployment=evidence / "runtime-deployment-v2/deployment.json",
        reuse_frozen=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    main(args.runtime.resolve())
