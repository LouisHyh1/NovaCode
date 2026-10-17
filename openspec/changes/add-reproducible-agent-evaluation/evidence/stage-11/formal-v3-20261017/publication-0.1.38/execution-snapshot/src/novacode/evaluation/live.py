"""完整 Live 题池的身份校验、初分层与事前确定顺序。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from novacode.evaluation.contracts import require


def row_digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def screen_pool(rows: list[dict[str, Any]], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """每一行保留筛选记录；身份冲突的全部行均不得入库。"""
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    for row in rows:
        identity, digest = row["instance_id"], row_digest(row)
        if identity in seen and seen[identity] != digest:
            conflicts.add(identity)
        seen[identity] = digest
    seen.clear()
    entries = []
    for index, row in enumerate(rows):
        identity = row["instance_id"]
        digest = row_digest(row)
        title = row["problem_statement"].splitlines()[0].strip().lower()
        difficulty = (
            "hard"
            if re.search(rule["hard_title_pattern"], title)
            else "easy"
            if re.search(rule["easy_title_pattern"], title)
            else "medium"
        )
        review = rule.get("complexity_reviews", {}).get(identity)
        if review is not None:
            require(review["difficulty"] in ("easy", "medium", "hard"), "审查难度无效")
            difficulty = review["difficulty"]
        reason = (
            "conflicting-duplicate"
            if identity in conflicts
            else "contract-review-excluded"
            if review and review.get("exclude_reason")
            else "identical-duplicate"
            if identity in seen
            else "outside-declared-repository-scope"
            if row["repo"] not in rule["repository_scope"]
            else "missing-official-contract"
            if not all(
                row[k] for k in ("issue_numbers", "FAIL_TO_PASS", "PASS_TO_PASS", "test_cmds")
            )
            else "eligible-for-review"
        )
        seen[identity] = digest
        entries.append(
            {
                "row_index": index,
                "instance_id": identity,
                "repository": row["repo"],
                "base_commit": row["base_commit"],
                "row_sha256": digest,
                "difficulty": difficulty,
                "difficulty_status": "reviewed" if review else "provisional-title-stratum",
                "complexity_review": review,
                "title": title,
                "reason": reason,
                "rank": hashlib.sha256(f"{rule['seed']}:{identity}".encode()).hexdigest(),
                "problem_keys": [
                    *(f"issue:{row['repo']}#{issue}" for issue in row["issue_numbers"]),
                    *(f"fix:{commit}" for commit in row["commit_urls"]),
                ],
            }
        )
    return entries


def candidate_order(entries: list[dict[str, Any]], difficulty: str) -> list[dict[str, Any]]:
    return sorted(
        (
            entry
            for entry in entries
            if entry["reason"] == "eligible-for-review" and entry["difficulty"] == difficulty
        ),
        key=lambda entry: (entry["rank"], entry["instance_id"]),
    )


def public_package(row: dict[str, Any]) -> dict[str, Any]:
    """重新构造白名单，不通过删除已知答案字段来复制未来未知字段。"""
    require(
        isinstance(row["problem_statement"], str) and bool(row["problem_statement"].strip()),
        "原始公开请求缺失",
    )
    return {"schema_version": 1, "requests": [row["problem_statement"]]}
