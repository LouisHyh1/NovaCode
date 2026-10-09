"""验证任务计数、资产边界、跨集污染及入库账本的负例。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from novacode.evaluation.assets import validate_campaign_tasks, validate_task_assets
from novacode.evaluation.contracts import (
    Asset,
    EvaluationCampaign,
    EvaluationRun,
    EvaluationTask,
    fingerprint,
    json_text,
)
from novacode.evaluation.corpus import Qualification, SelectionLedger, quota_report, validate_corpus


def asset(root: Path, name: str, content: str) -> Asset:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return Asset(name, hashlib.sha256(path.read_bytes()).hexdigest())


def task(
    public: Path, hidden: Path, task_id: str, *, turns: int = 1, live: bool = False
) -> EvaluationTask:
    return EvaluationTask(
        task_id=task_id,
        candidate_id=task_id,
        source="live" if live else "specialty",
        suite="development",
        family_id=f"family:{task_id}",
        problem_keys=(
            f"issue:example/repo#{int(task_id[1:])}" if live else f"defect:example/{task_id}",
        ),
        repository="example/repo",
        source_revision="b" * 40,
        base_commit="a" * 40,
        difficulty="easy",
        category="issue-fix" if live else "localization",
        pressure_tags=(),
        budget_category="small",
        initial_state=asset(public, task_id + "/initial.txt", f"initial-{task_id}"),
        public_input=asset(
            public,
            task_id + "/requests.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "requests": [f"{task_id} 请求 {index}" for index in range(turns)],
                }
            ),
        ),
        environment=asset(public, task_id + "/environment.json", '{"python":"3.12"}'),
        acceptance_assets=(
            asset(hidden, task_id + "/acceptance.json", '{"conditions":["facts"]}'),
        ),
        acceptance_conditions=("behavior", "regression", "scope", "cleanup"),
        allowed_paths=(),
        builder_id="shared-builder-v1",
    )


def run(candidate: EvaluationTask, *, turns: int = 1, **kwargs: object) -> EvaluationRun:
    fields = dict(
        run_id="r-" + candidate.task_id,
        campaign_id="campaign",
        task_id=candidate.task_id,
        task_sha256=fingerprint(candidate),
        initial_sha256=candidate.initial_state.sha256,
        config_id="full",
        repeat_id=1,
        state="RECORDED",
        agent_run_ids=tuple(f"{candidate.task_id}-agent-{i}" for i in range(turns)),
        completed_requests=turns,
        termination="completed",
        acceptance="passed",
        scope="passed",
        cleanup="passed",
    )
    fields.update(kwargs)
    return EvaluationRun(**fields)


def campaign(
    tasks: tuple[EvaluationTask, ...], runs: tuple[EvaluationRun, ...]
) -> EvaluationCampaign:
    return EvaluationCampaign(
        campaign_id="campaign",
        suite="development",
        task_ids=tuple(t.task_id for t in tasks),
        config_ids=("full",),
        protocol_sha256="1" * 64,
        source_sha256="2" * 64,
        dependency_sha256="3" * 64,
        task_manifest_sha256="4" * 64,
        environment_sha256="5" * 64,
        parameters_sha256="6" * 64,
        budget_sha256="7" * 64,
        analysis_sha256="8" * 64,
        seed=42,
        concurrency=1,
        runs=runs,
    )


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    public, hidden = tmp_path / "public", tmp_path / "external"
    public.mkdir()
    hidden.mkdir()
    return public, hidden


def test_single_and_multiturn_count_and_json_roundtrip(roots: tuple[Path, Path]) -> None:
    public, hidden = roots
    tasks = task(public, hidden, "S01"), task(public, hidden, "S02", turns=3)
    value = campaign(tasks, (run(tasks[0]), run(tasks[1], turns=3)))
    validate_campaign_tasks(value, tasks, public, hidden, frozenset({"small"}))
    assert value.counts == {"evaluation_tasks": 2, "evaluation_runs": 2, "agent_runs": 4}
    assert EvaluationTask.from_json(json_text(tasks[0])) == tasks[0]
    assert EvaluationRun.from_json(json_text(value.runs[0])) == value.runs[0]
    assert EvaluationCampaign.from_json(json_text(value)) == value
    assert "team_task" not in json_text(value)


@pytest.mark.parametrize(
    "changes,strict",
    [
        ({}, True),
        ({"acceptance": "failed"}, False),
        ({"termination": "timeout", "live_resolved": True}, False),
        ({"termination": "budget-exhausted"}, False),
        ({"scope": "failed"}, False),
        ({"cleanup": "failed"}, False),
        ({"acceptance": "ungradable"}, False),
    ],
)
def test_termination_acceptance_and_cleanup_are_independent(
    roots: tuple[Path, Path],
    changes: dict[str, object],
    strict: bool,
) -> None:
    value = run(task(*roots, "L01", live=True), **changes)
    assert value.strict_success is strict
    if changes.get("termination") == "timeout":
        assert value.live_resolved is True and value.acceptance == "passed"


def test_preflight_block_does_not_create_agent_result(roots: tuple[Path, Path]) -> None:
    candidate = task(*roots, "S01")
    planned = EvaluationRun(
        run_id="p",
        campaign_id="campaign",
        task_id=candidate.task_id,
        task_sha256=fingerprint(candidate),
        initial_sha256=candidate.initial_state.sha256,
        config_id="full",
        repeat_id=1,
        state="PREFLIGHT",
        block_reason="container denied",
    )
    assert not planned.strict_success and planned.termination is None
    assert planned.agent_run_ids == ()
    with pytest.raises(ValueError, match="未开始"):
        replace(planned, termination="runner-error")


def test_skipped_script_and_changed_identity_cannot_count_as_success(
    roots: tuple[Path, Path],
) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "S01", turns=3)
    incomplete = campaign((candidate,), (run(candidate),))
    with pytest.raises(ValueError, match="省略"):
        validate_campaign_tasks(incomplete, (candidate,), public, hidden, frozenset({"small"}))
    wrong = campaign((candidate,), (run(candidate, turns=3, task_sha256="0" * 64),))
    with pytest.raises(ValueError, match="指纹"):
        validate_campaign_tasks(wrong, (candidate,), public, hidden, frozenset({"small"}))


def test_retry_keeps_original_and_plan_keys_unique(roots: tuple[Path, Path]) -> None:
    candidate = task(*roots, "S01")
    original = run(candidate, termination="timeout")
    retry = replace(original, run_id="retry", retry_of=original.run_id, termination="completed")
    value = campaign((candidate,), (original, retry))
    assert (
        len(value.runs) == 2 and not value.runs[0].strict_success and value.runs[1].strict_success
    )
    with pytest.raises(ValueError, match="原计划键"):
        campaign((candidate,), (original, replace(original, run_id="duplicate")))
    with pytest.raises(ValueError, match="补跑"):
        campaign((candidate,), (replace(retry, retry_of="missing"),))


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": ""},
        {"schema_version": 2},
        {"base_commit": "main"},
        {"source_revision": "verified", "source": "live", "category": "issue-fix"},
        {"acceptance_assets": ()},
        {"problem_keys": ()},
        {"acceptance_conditions": ()},
        {"allowed_paths": ("../outside",)},
        {"allowed_paths": ("/absolute",)},
        {"allowed_paths": (".novacode/config.yaml",)},
        {"difficulty": "pressure"},
    ],
)
def test_incomplete_task_is_rejected(roots: tuple[Path, Path], changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(task(*roots, "S01"), **changes)


def test_json_rejects_missing_version_unknown_fields_and_duplicate_keys(
    roots: tuple[Path, Path],
) -> None:
    data = asdict(task(*roots, "S01"))
    data.pop("schema_version")
    with pytest.raises(ValueError, match="schema_version"):
        EvaluationTask.from_json(json.dumps(data))
    with pytest.raises(ValueError, match="重复 JSON"):
        EvaluationTask.from_json('{"schema_version":1,"schema_version":1}')
    data["schema_version"] = 1
    data["team_task_id"] = "forbidden"
    with pytest.raises(TypeError):
        EvaluationTask.from_json(json.dumps(data))


def test_missing_assets_bad_hash_undefined_budget_and_hidden_overlap(
    roots: tuple[Path, Path],
) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "S01")
    with pytest.raises(ValueError, match="预算"):
        validate_task_assets(candidate, public, hidden, frozenset())
    with pytest.raises(ValueError, match="分离"):
        validate_task_assets(candidate, public, public, frozenset({"small"}))
    with pytest.raises(ValueError, match="分离"):
        validate_task_assets(candidate, public, public / "S01", frozenset({"small"}))
    with pytest.raises(ValueError, match="指纹"):
        validate_task_assets(
            replace(candidate, initial_state=replace(candidate.initial_state, sha256="0" * 64)),
            public,
            hidden,
            frozenset({"small"}),
        )
    (hidden / candidate.acceptance_assets[0].path).unlink()
    with pytest.raises(FileNotFoundError):
        validate_task_assets(candidate, public, hidden, frozenset({"small"}))
    with pytest.raises(ValueError, match="SHA-256"):
        Asset("input.json", "placeholder")


def test_symlink_asset_escape_is_rejected(roots: tuple[Path, Path]) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "S01")
    path = public / candidate.initial_state.path
    path.unlink()
    try:
        path.symlink_to(hidden / candidate.acceptance_assets[0].path)
    except OSError:
        pytest.skip("当前执行身份不能创建符号链接")
    with pytest.raises(ValueError, match="越界"):
        validate_task_assets(candidate, public, hidden, frozenset({"small"}))


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "requests": []},
        {"schema_version": 1, "requests": ["request"], "patch": "answer"},
        {"schema_version": True, "requests": ["request"]},
        {"schema_version": 1, "requests": ["request", "extra"]},
    ],
)
def test_live_input_whitelist_and_single_rollout(
    roots: tuple[Path, Path], payload: dict[str, object]
) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "L01", live=True)
    input_asset = asset(public, candidate.public_input.path, json.dumps(payload))
    with pytest.raises(ValueError):
        validate_task_assets(
            replace(candidate, public_input=input_asset), public, hidden, frozenset({"small"})
        )


@pytest.mark.parametrize("duplicate", ["id", "candidate", "content", "issue", "family", "defect"])
def test_cross_suite_issue_renamed_defect_and_duplicate_candidate(
    roots: tuple[Path, Path],
    duplicate: str,
) -> None:
    first = task(*roots, "S01")
    second = replace(task(*roots, "S02"), suite="frozen")
    changes = {
        "id": {"task_id": first.task_id},
        "candidate": {"candidate_id": first.candidate_id},
        "content": {"initial_state": first.initial_state, "public_input": first.public_input},
        "family": {"family_id": first.family_id},
        "defect": {"problem_keys": first.problem_keys},
        "issue": {"problem_keys": ("issue:example/repo#1", "defect:example/S02")},
    }
    if duplicate == "issue":
        first = replace(first, problem_keys=("issue:example/repo#1", "defect:example/S01"))
    with pytest.raises(ValueError):
        validate_corpus((first, replace(second, **changes[duplicate])))


def test_shared_builder_is_allowed_when_business_families_are_independent(
    roots: tuple[Path, Path],
) -> None:
    first = task(*roots, "S01")
    second = replace(task(*roots, "S02"), suite="frozen")
    validate_corpus((first, second))
    assert first.builder_id == second.builder_id


def test_problem_keys_cannot_evade_isolation_by_case_or_trailing_slash(
    roots: tuple[Path, Path],
) -> None:
    first = task(*roots, "S01")
    second = replace(task(*roots, "S02"), suite="frozen", problem_keys=("defect:EXAMPLE/S01/",))
    with pytest.raises(ValueError, match="同源问题"):
        validate_corpus((first, second))


def proof(candidate: EvaluationTask, hidden: Path, **changes: object) -> Asset:
    fields = dict(
        task_sha256=fingerprint(candidate),
        environment_verified=True,
        resources_verified=True,
        original_target_failed=True,
        original_regressions_passed=True,
        reference_passes=(True, True, True),
        correct_implementation_passed=True,
        negative_controls_passed=True,
        evidence=candidate.acceptance_assets,
    )
    fields.update(changes)
    return asset(
        hidden, candidate.task_id + "/qualification.json", json_text(Qualification(**fields))
    )


def test_candidates_are_not_admitted_and_admission_replays_after_restart(
    roots: tuple[Path, Path],
) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "L01", live=True)
    ledger = SelectionLedger(
        hidden / "selection.jsonl", (candidate,), roots[0], hidden, frozenset({"small"})
    )
    states, _ = ledger.replay()
    assert quota_report((candidate,), states)["rows"][0]["admitted"] == 0
    ledger.append(candidate.task_id, "SELECT", "selection-rule", "按事前规则选择")
    with pytest.raises(ValueError, match="缺少"):
        ledger.append(candidate.task_id, "ADMIT", "qualification-passed", "无证据不能入库")
    ledger.append(
        candidate.task_id,
        "ADMIT",
        "qualification-passed",
        "原始失败和参考三次通过",
        evidence=proof(candidate, hidden),
    )
    restarted = SelectionLedger(ledger.path, (candidate,), roots[0], hidden, frozenset({"small"}))
    states, events = restarted.replay()
    assert len(events) == 2 and states[candidate.task_id] == "ADMITTED"
    row = quota_report((candidate,), states)["rows"][0]
    assert row["candidates"] == row["admitted"] == 1 and row["gap"] == 11


@pytest.mark.parametrize(
    "changes",
    [
        {"reference_passes": (True, False, True)},
        {"reference_passes": (True, True)},
        {"original_target_failed": False},
        {"original_regressions_passed": False},
        {"environment_verified": False},
        {"resources_verified": False},
        {"task_sha256": "0" * 64},
        {"evidence": ()},
    ],
)
def test_invalid_live_qualification_cannot_admit(
    roots: tuple[Path, Path],
    changes: dict[str, object],
) -> None:
    candidate = task(*roots, "L01", live=True)
    hidden = roots[1]
    ledger = SelectionLedger(
        hidden / "selection.jsonl", (candidate,), roots[0], hidden, frozenset({"small"})
    )
    ledger.append(candidate.task_id, "SELECT", "selection-rule", "候选")
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError):
        ledger.append(
            candidate.task_id,
            "ADMIT",
            "qualification-passed",
            "无效核验",
            evidence=proof(candidate, hidden, **changes),
        )
    assert ledger.path.read_bytes() == before


def test_specialty_negative_controls_required(roots: tuple[Path, Path]) -> None:
    candidate = task(*roots, "S01")
    hidden = roots[1]
    ledger = SelectionLedger(
        hidden / "selection.jsonl", (candidate,), roots[0], hidden, frozenset({"small"})
    )
    ledger.append(candidate.task_id, "SELECT", "selection-rule", "候选")
    with pytest.raises(ValueError, match="负例"):
        ledger.append(
            candidate.task_id,
            "ADMIT",
            "qualification-passed",
            "缺陷未检出",
            evidence=proof(candidate, hidden, negative_controls_passed=False),
        )


def test_admission_rechecks_public_assets(roots: tuple[Path, Path]) -> None:
    public, hidden = roots
    candidate = task(public, hidden, "S01")
    ledger = SelectionLedger(
        hidden / "selection.jsonl", (candidate,), public, hidden, frozenset({"small"})
    )
    ledger.append(candidate.task_id, "SELECT", "selection-rule", "候选")
    qualification = proof(candidate, hidden)
    (public / candidate.initial_state.path).unlink()
    before = ledger.path.read_bytes()
    with pytest.raises(FileNotFoundError):
        ledger.append(
            candidate.task_id,
            "ADMIT",
            "qualification-passed",
            "缺少初始资产",
            evidence=qualification,
        )
    assert ledger.path.read_bytes() == before


def test_exclusion_replacement_and_frozen_immutability(roots: tuple[Path, Path]) -> None:
    tasks = tuple(replace(task(*roots, f"S{i:02}"), suite="frozen") for i in range(1, 5))
    hidden = roots[1]
    ledger = SelectionLedger(
        hidden / "selection.jsonl", tasks, roots[0], hidden, frozenset({"small"})
    )
    ledger.append("S01", "SELECT", "selection-rule", "规则选择")
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="Agent"):
        ledger.append("S01", "REPLACE", "agent-failed", "因 Agent 失败换题", replacement_id="S02")
    with pytest.raises(ValueError, match="转换"):
        ledger.append("S03", "FREEZE", "campaign-frozen", "候选不能冻结")
    assert ledger.path.read_bytes() == before
    ledger.append("S01", "REPLACE", "environment-invalid", "环境核验失败", replacement_id="S02")
    ledger.append("S03", "EXCLUDE", "family-conflict", "事前隔离规则")
    ledger.append(
        "S02", "ADMIT", "qualification-passed", "正确与负例已核验", evidence=proof(tasks[1], hidden)
    )
    freeze = asset(
        hidden,
        "freeze.json",
        json.dumps({"schema_version": 1, "suite": "frozen", "task_sha256": fingerprint(tasks[1])}),
    )
    ledger.append("S02", "FREEZE", "campaign-frozen", "固定合同", evidence=freeze)
    assert ledger.replay()[0] == {
        "S01": "REPLACED",
        "S02": "FROZEN",
        "S03": "EXCLUDED",
        "S04": "CANDIDATE",
    }
    with pytest.raises(ValueError, match="转换"):
        ledger.append("S02", "REPLACE", "contract-invalid", "冻结后不可换题", replacement_id="S04")


def test_replacement_preserves_stratum_and_ledger_detects_tamper(roots: tuple[Path, Path]) -> None:
    tasks = task(*roots, "S01"), replace(task(*roots, "S02"), difficulty="hard")
    hidden = roots[1]
    ledger = SelectionLedger(
        hidden / "selection.jsonl", tasks, roots[0], hidden, frozenset({"small"})
    )
    ledger.append("S01", "SELECT", "selection-rule", "规则")
    with pytest.raises(ValueError, match="配额"):
        ledger.append("S01", "REPLACE", "quota-rule", "不许跨层替代", replacement_id="S02")
    changed = replace(tasks[0], budget_category="different")
    with pytest.raises(ValueError, match="合同已改变"):
        SelectionLedger(
            ledger.path, (changed, tasks[1]), roots[0], hidden, frozenset({"small"})
        ).replay()
    ledger.path.write_text(ledger.path.read_text().rstrip(), encoding="utf-8")
    with pytest.raises(ValueError, match="尾行"):
        ledger.replay()


def test_quota_reports_all_source_difficulty_and_category_gaps(roots: tuple[Path, Path]) -> None:
    tasks = tuple(task(*roots, f"S{i:02}") for i in range(1, 4))
    states = {t.task_id: "CANDIDATE" for t in tasks}
    states["S01"] = "ADMITTED"
    row = quota_report(tasks, states)["rows"][1]
    assert row["candidates"] == 3 and row["admitted"] == 1
    assert row["difficulty_gaps"] == {"easy": 3, "medium": 6, "hard": 2}
    assert row["category_targets"] == {
        "localization": 2,
        "test-addition": 2,
        "tool-failure": 2,
        "long-context": 3,
        "tool-discovery": 3,
    }
    assert quota_report(tasks, states)["rows"][3]["target"] == 24
    with pytest.raises(ValueError, match="专项配额"):
        quota_report(tasks, {t.task_id: "ADMITTED" for t in tasks})


def test_full_72_task_target_and_each_source_stratum(roots: tuple[Path, Path]) -> None:
    tasks = []
    for suite, factor in (("development", 1), ("frozen", 2)):
        for source in ("live", "specialty"):
            difficulties = (
                ["easy"] * (4 * factor) + ["medium"] * (6 * factor) + ["hard"] * (2 * factor)
            )
            categories = (
                ["localization"] * (2 * factor)
                + ["test-addition"] * (2 * factor)
                + ["tool-failure"] * (2 * factor)
                + ["long-context"] * (3 * factor)
                + ["tool-discovery"] * (3 * factor)
            )
            for difficulty, category in zip(difficulties, categories, strict=True):
                candidate = task(*roots, f"T{len(tasks) + 1:02}", live=source == "live")
                tasks.append(
                    replace(
                        candidate,
                        suite=suite,
                        difficulty=difficulty,
                        category="issue-fix" if source == "live" else category,
                    )
                )
    values = tuple(tasks)
    report = quota_report(values, {t.task_id: "ADMITTED" for t in values})
    assert len(values) == 72
    assert [row["admitted"] for row in report["rows"]] == [12, 12, 24, 24]
    for row in report["rows"]:
        assert row["gap"] == 0 and not any(row["difficulty_gaps"].values())
        assert not any(row["category_gaps"].values())
