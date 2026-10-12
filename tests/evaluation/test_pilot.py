"""先导计划必须在真实调用之前拒绝缺预算、越界题集和变化身份。"""

import copy
import json
import os
from pathlib import Path

import pytest

from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.pilot import plan_runs, validate_policy

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    policy = json.loads((ROOT / "evaluation/pilot/policy.json").read_text())
    tasks = tuple(
        EvaluationTask.from_json(p.read_text())
        for source in ("live", "specialty")
        for p in sorted((ROOT / "evaluation" / source / "assets/contracts").glob("*.json"))
    )
    return policy, tasks


def test_pilot_plan_balances_orders_and_budgets():
    policy, tasks = inputs()
    plan = plan_runs(policy, tasks)
    assert len(plan) == len({row["run_id"] for row in plan}) == 72
    assert [row["task_id"] for row in plan[:6]] == ["S01"] * 3 + ["L02"] * 3
    orders = {}
    for start in range(0, len(plan), 3):
        block = plan[start : start + 3]
        assert len({json.dumps(r["limits"], sort_keys=True) for r in block}) == 1
        assert len({r["task_id"] for r in block}) == 1
        order = tuple(r["config_id"] for r in block)
        orders[order] = orders.get(order, 0) + 1
    assert len(orders) == 6 and set(orders.values()) == {4}


@pytest.mark.parametrize("block", ["temporary", "long_context", "total", "probe"])
@pytest.mark.parametrize(
    "field",
    [
        "seconds",
        "tokens",
        "provider_calls",
        "tool_calls",
        "single_request_tokens",
        "cleanup_seconds",
    ],
)
def test_missing_budget_rejects_plan_before_call(block, field):
    policy, tasks = inputs()
    del policy[block][field]
    with pytest.raises((TypeError, ValueError, KeyError)):
        plan_runs(policy, tasks)


@pytest.mark.parametrize(
    "field",
    [
        "seed",
        "context_window",
        "endpoint_context_tokens",
        "max_output_tokens",
        "timeout_seconds",
        "sdk_max_retries",
    ],
)
def test_missing_request_parameters_reject_plan(field):
    policy, tasks = inputs()
    del policy[field]
    with pytest.raises((TypeError, ValueError, KeyError)):
        validate_policy(policy, tasks)


def test_frozen_or_inadequate_total_rejects_plan():
    policy, tasks = inputs()
    from dataclasses import replace

    with pytest.raises(ValueError, match="开发题"):
        plan_runs(policy, (replace(tasks[0], suite="frozen"), *tasks[1:]))
    limited = copy.deepcopy(policy)
    limited["total"]["tokens"] = 240000
    with pytest.raises(ValueError, match="总预算不足"):
        plan_runs(limited, tasks)


def test_calibration_preserves_all_failed_and_censored_samples():
    from evaluation.pilot.review import FROZEN_COUNTS, calibration

    samples = [
        {
            "budget_category": category,
            "result": {"run_id": category, "termination": "budget-exhausted"},
            "limits": {"seconds": 300, "tokens": 600000, "provider_calls": 24, "tool_calls": 50},
            "metrics": {
                "main_end_to_end_seconds": 10,
                "known_token_lower_bound": 100,
                "provider_calls": 1,
                "tool_calls": 1,
            },
            "max_request_tokens": 100,
            "max_input_estimate": 100,
            "max_request_bytes": 350,
            "outer_metrics": {"phase_seconds": {"cleanup": 2}},
        }
        for category in FROZEN_COUNTS
    ]
    data = calibration(samples)
    assert data["state"] == "CALIBRATED_NOT_FROZEN" and data["formal_model_calls"] == 0
    for category, value in data["categories"].items():
        assert value["evidence_runs"] == value["censored_runs"] == [category]
        assert value["limits"]["tokens"] == 900000
    assert data["total"]["tokens"] >= 900000 * 432


@pytest.mark.skipif(os.name != "posix", reason="Linux先导预算使用flock")
def test_calibration_shares_envelope_and_does_not_refund_failed_reservations(tmp_path):
    from dataclasses import replace

    from evaluation.pilot.calibrate import available, plan
    from novacode.evaluation.budget import BudgetLimits
    from novacode.evaluation.campaign import reserve_run

    policy, _ = inputs()
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    (tmp_path / "runs").mkdir()
    total = BudgetLimits(**policy["total"])
    reserve_run(
        tmp_path / "runs",
        "envelope",
        "failed-unknown",
        replace(BudgetLimits(**policy["temporary"]), tokens=59500000),
        total,
    )
    rows = plan(tmp_path)
    assert len(rows) == 6 and len({r["run_id"] for r in rows}) == 6
    with pytest.raises(ValueError, match="跨轮总预算不足"):
        available(tmp_path, rows)
