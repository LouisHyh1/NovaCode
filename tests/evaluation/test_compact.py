"""元数据抽样的可行性、稳定性与结果隔离。"""

import json
from collections import Counter
from pathlib import Path

import pytest

from novacode.evaluation.compact import (
    CATEGORY,
    DIFFICULTY,
    PARENT_SHA,
    PRESSURE,
    metadata,
    selection,
)

ROOT = Path(__file__).resolve().parents[2]


def parent_metadata():
    parent = json.loads((ROOT / "evaluation/frozen/campaign-v2/manifest.json").read_text())
    return [
        metadata(json.loads(p.read_text()))
        for root in parent["asset_roots"].values()
        for p in (ROOT / root / "contracts").glob("*.json")
    ]


def test_compact_selection_is_fixed_metadata_only_and_all_quotas_hold():
    candidates = parent_metadata()
    result = selection(candidates, PARENT_SHA)
    assert result == selection(list(reversed(candidates)), PARENT_SHA)
    tuple_metadata = [{**t, "pressure_tags": tuple(t["pressure_tags"])} for t in candidates]
    assert json.loads(json.dumps(result)) == selection(tuple_metadata, PARENT_SHA)
    assert result == selection(
        [{**t, "success_rate": 1, "usage": "ignored"} for t in candidates], PARENT_SHA
    )
    for source, ids in result["selected"].items():
        chosen = [t for t in candidates if t["task_id"] in ids]
        assert Counter(t["difficulty"] for t in chosen) == DIFFICULTY
        assert len(chosen) == 12
        if source == "live":
            counts = Counter(t["repository"] for t in chosen)
            assert len(counts) >= 8 and max(counts.values()) <= 2
        else:
            assert Counter(t["category"] for t in chosen) == CATEGORY
            assert PRESSURE <= {tag for t in chosen for tag in t["pressure_tags"]}
    with pytest.raises(ValueError, match="父 manifest"):
        selection(candidates, "0" * 64)


def test_compact_infeasible_metadata_stops_without_relaxing_quotas():
    candidates = parent_metadata()
    candidates = [
        {**t, "repository": "single-repository"} if t["source"] == "live" else t for t in candidates
    ]
    with pytest.raises(ValueError, match="不可行"):
        selection(candidates, PARENT_SHA)


def frozen_pool():
    from novacode.evaluation.frozen import load_tasks

    parent = json.loads((ROOT / "evaluation/frozen/campaign-v2/manifest.json").read_text())
    tasks = tuple(t for path in parent["asset_roots"].values() for t in load_tasks(ROOT / path))
    chosen = selection(parent_metadata(), PARENT_SHA)
    ids = {name for names in chosen["selected"].values() for name in names}
    return parent, tasks, tuple(t for t in tasks if t.task_id in ids)


def test_plan_budget_and_six_orders_use_campaign_size_and_preserve_old_identity():
    from novacode.evaluation.compact_manifest import compact_budgets
    from novacode.evaluation.frozen import plan_runs

    parent, pool, tasks = frozen_pool()
    assert (
        plan_runs(pool, parent["budgets"], parent["campaign_id"], parent["policy"]["seed"])
        == parent["plan"]
    )
    budgets = compact_budgets(parent["budgets"], tasks)
    plan = plan_runs(tasks, budgets, "compact-fixture", 20261012, expected_tasks=24)
    assert len(plan) == len({r["run_id"] for r in plan}) == 216
    assert Counter(r["config_id"] for r in plan) == {
        "full": 72,
        "no-compression": 72,
        "eager-schema": 72,
    }
    for source in ("live", "specialty"):
        ids = {t.task_id for t in tasks if t.source == source}
        orders = Counter(
            tuple(r["config_id"] for r in plan[i : i + 3])
            for i in range(0, len(plan), 3)
            if plan[i]["task_id"] in ids
        )
        assert len(orders) == 6 and set(orders.values()) == {6}
    with pytest.raises(ValueError, match="独立任务数"):
        plan_runs(tasks, budgets, "bad", 1)


@pytest.mark.parametrize("compact", [True, False])
def test_report_status_denominators_are_derived_and_cross_campaign_is_rejected(tmp_path, compact):
    from evaluation.formal.report import report
    from evaluation.formal.status import status
    from novacode.evaluation.compact_manifest import compact_budgets, subset
    from novacode.evaluation.frozen import digest, plan_runs

    parent, pool, chosen = frozen_pool()
    tasks = chosen if compact else pool
    manifest = {**parent, "campaign_id": "fixture"}
    budgets = compact_budgets(parent["budgets"], tasks) if compact else parent["budgets"]
    manifest["corpus"] = subset(parent["corpus"], {t.task_id for t in tasks})
    manifest["plan"] = plan_runs(tasks, budgets, "fixture", 20261012, expected_tasks=len(tasks))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    root = tmp_path / "queue"
    (root / "runs").mkdir(parents=True)
    (root / "identity.json").write_text(
        json.dumps({"manifest_sha256": digest(path), "campaign_id": "fixture"})
    )
    report(root, tmp_path / "report", path)
    summary = json.loads((tmp_path / "report/summary.json").read_text())
    assert summary["planned"] == (216 if compact else 432)
    assert summary["sources"]["live"]["full"]["planned"] == (36 if compact else 72)
    assert status(root, path)["planned"] == len(manifest["plan"])
    (root / "runs/outside").mkdir()
    (root / "runs/outside/result.json").write_text(
        json.dumps({"run_id": "outside", "campaign_id": "old", "state": "RECORDED"})
    )
    with pytest.raises(ValueError, match="campaign"):
        status(root, path)
    with pytest.raises(ValueError, match="campaign"):
        report(root, tmp_path / "mixed-report", path)


async def test_unapproved_campaign_cannot_reach_environment_or_provider(tmp_path, monkeypatch):
    from evaluation.formal.run import execute

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    manifest.with_suffix(".sha256").write_text("a" * 64)
    monkeypatch.setattr(
        "evaluation.formal.run.verify_manifest", lambda *args: {"schema_version": 2}
    )
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="用户明确确认"):
        await execute(root, 3, manifest_path=manifest)
    assert not root.exists()


def test_authorization_is_bound_to_identity_and_budget_and_disk_stops(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from novacode.evaluation.compact_manifest import check_disk, validate_authorization
    from novacode.evaluation.frozen import identity

    data = {
        "campaign_id": "new",
        "budgets": {"total": {"tokens": 100}},
        "disk_admission": {"minimum_free_bytes": 1000},
    }
    approved = {
        "approved": True,
        "campaign_id": "new",
        "manifest_sha256": "a" * 64,
        "budget_sha256": identity(data["budgets"]["total"]),
    }
    validate_authorization(data, approved, "a" * 64)
    with pytest.raises(ValueError, match="启动确认"):
        validate_authorization(data, {**approved, "campaign_id": "old"}, "a" * 64)
    monkeypatch.setattr("shutil.disk_usage", lambda root: SimpleNamespace(free=999))
    with pytest.raises(ValueError, match="停止新任务"):
        check_disk(tmp_path, data)
