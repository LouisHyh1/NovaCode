"""compact-v3-selection-v1：只接受父任务元数据，字典序最小可行子集。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from novacode.evaluation.contracts import require

METHOD = "compact-v3-selection-v1"
SEED = 20261016
PARENT_SHA = "cf7191aeef652b2658456572fb737c17791649e65870ebba48812d1dd35de658"
DIFFICULTY = {"easy": 4, "medium": 6, "hard": 2}
CATEGORY = {
    "localization": 2,
    "test-addition": 2,
    "tool-failure": 2,
    "long-context": 3,
    "tool-discovery": 3,
}
PRESSURE = frozenset(
    {
        "first-grep-error",
        "first-test-timeout",
        "early-constraints",
        "large-file",
        "multi-stage",
        "scripted-history",
        "builtin-discovery",
        "mcp-discovery",
        "similar-responsibilities",
        "cross-record",
    }
)
FIELDS = (
    "task_id",
    "source",
    "difficulty",
    "category",
    "repository",
    "family_id",
    "pressure_tags",
    "budget_category",
)


def metadata(task: dict[str, Any]) -> dict[str, Any]:
    return {**{key: task[key] for key in FIELDS}, "pressure_tags": list(task["pressure_tags"])}


def key(task_id: str, parent_sha: str = PARENT_SHA) -> str:
    raw = json.dumps(
        [METHOD, parent_sha.lower(), SEED, task_id], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def partial_allowed(selected: list[dict[str, Any]], source: str) -> bool:
    difficulties = Counter(t["difficulty"] for t in selected)
    categories = Counter(t["category"] for t in selected)
    repositories = Counter(t["repository"] for t in selected)
    return (
        all(difficulties[d] <= n for d, n in DIFFICULTY.items())
        and (source != "specialty" or all(categories[c] <= n for c, n in CATEGORY.items()))
        and (source != "live" or all(n <= 2 for n in repositories.values()))
        and len({t["family_id"] for t in selected}) == len(selected)
    )


def search(
    ordered: list[dict[str, Any]], source: str, start: int, selected: list[dict[str, Any]]
) -> list[str] | None:
    if not partial_allowed(selected, source):
        return None
    missing = 12 - len(selected)
    if missing == 0:
        if source == "live" and len({t["repository"] for t in selected}) < 8:
            return None
        tags = {tag for t in selected for tag in t["pressure_tags"]}
        if source == "specialty" and not PRESSURE <= tags:
            return None
        return [t["task_id"] for t in selected]
    remaining = ordered[start:]
    if len(remaining) < missing:
        return None
    counts = Counter(t["difficulty"] for t in remaining)
    difficulties = Counter(t["difficulty"] for t in selected)
    if any(counts[d] + difficulties[d] < n for d, n in DIFFICULTY.items()):
        return None
    for index in range(start, len(ordered) - missing + 1):
        found = search(ordered, source, index + 1, [*selected, ordered[index]])
        if found is not None:
            return found
    return None


def select_source(candidates: list[dict[str, Any]], source: str) -> list[str]:
    """按候选序号 DFS，首个完整叶子即全部可行集合中字典序最小者。"""
    ordered = sorted(
        (metadata(t) for t in candidates if t["source"] == source),
        key=lambda t: (key(t["task_id"]), t["task_id"]),
    )
    result = search(ordered, source, 0, [])
    require(result is not None, f"{source} 固定配额不可行，停止并等待协议修订")
    assert result is not None
    return result


def selection(candidates: list[dict[str, Any]], parent_sha: str) -> dict[str, Any]:
    require(parent_sha == PARENT_SHA, "父 manifest 身份变化")
    candidates = [metadata(t) for t in candidates]
    require(
        len(candidates) == len({t["task_id"] for t in candidates}) == 48, "父元数据需要48个唯一任务"
    )
    require(
        Counter(t["source"] for t in candidates) == {"live": 24, "specialty": 24}, "父来源数量变化"
    )
    selected = {source: select_source(candidates, source) for source in ("live", "specialty")}
    selected_ids = {name for names in selected.values() for name in names}
    ordered = sorted(candidates, key=lambda t: (key(t["task_id"]), t["task_id"]))
    raw = json.dumps(
        sorted(candidates, key=lambda t: t["task_id"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "schema_version": 1,
        "method": METHOD,
        "seed": SEED,
        "parent_manifest_sha256": parent_sha,
        "input_metadata_sha256": hashlib.sha256(raw).hexdigest(),
        "selected": selected,
        "candidates": [
            {
                "metadata": t,
                "rank": i,
                "hash": key(t["task_id"]),
                "selected": t["task_id"] in selected_ids,
                "reason": "lexicographically-first-feasible-set"
                if t["task_id"] in selected_ids
                else "not-in-lexicographically-first-feasible-set",
            }
            for i, t in enumerate(ordered)
        ],
        "constraints": {
            "per_source": 12,
            "difficulty": DIFFICULTY,
            "specialty_categories": CATEGORY,
            "live_repository_max": 2,
            "live_repository_min": 8,
            "pressure_union": sorted(PRESSURE),
        },
        "results_read": False,
    }
