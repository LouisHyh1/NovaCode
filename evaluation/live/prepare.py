"""核对固定 revision 的 LFS 身份并保存完整候选池，原始答案留在外部缓存。"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from collections import Counter
from pathlib import Path

import pyarrow.parquet as parquet

from novacode.evaluation.live import candidate_order, screen_pool


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def prepare(dataset: Path, output: Path, rule_path: Path) -> None:
    rule = json.loads(rule_path.read_text())
    url = (
        "https://huggingface.co/api/datasets/SWE-bench-Live/SWE-bench-Live/tree/"
        + rule["dataset_revision"]
        + "/data"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.load(response)
    item = next(item for item in metadata if item["path"] == "data/verified-00000-of-00001.parquet")
    actual = hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert actual == item["lfs"]["oid"] == rule["dataset_sha256"]
    assert dataset.stat().st_size == item["lfs"]["size"]
    rows = parquet.read_table(dataset).to_pylist()
    assert len(rows) == 500
    output.mkdir(parents=True, exist_ok=False)
    save(
        output / "dataset-identity.json",
        {
            "schema_version": 1,
            "revision": rule["dataset_revision"],
            "metadata_url": url,
            "remote_file": item,
            "sha256": actual,
            "rows": len(rows),
            "model_calls": 0,
        },
    )
    save(output / "selection-rule.json", rule)
    entries = screen_pool(rows, rule)
    save(output / "pool.json", {"schema_version": 1, "rows": entries})
    save(
        output / "pool-summary.json",
        {
            "schema_version": 1,
            "rows": len(entries),
            "screening": dict(Counter(e["reason"] for e in entries)),
            "strata": {
                level: len(candidate_order(entries, level)) for level in rule["stratum_order"]
            },
        },
    )
    for level in rule["stratum_order"]:
        print(
            level,
            [(e["instance_id"], e["row_index"]) for e in candidate_order(entries, level)[:12]],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--rule", type=Path, default=Path(__file__).with_name("selection-rule.json")
    )
    args = parser.parse_args()
    prepare(args.dataset, args.output, args.rule)
