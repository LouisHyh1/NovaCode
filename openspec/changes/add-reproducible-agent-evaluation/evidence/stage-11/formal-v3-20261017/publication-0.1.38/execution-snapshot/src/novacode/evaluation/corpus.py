"""题库隔离、配额审计与单写入者追加账本，不依据 Agent 成败选题。"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novacode.evaluation.assets import validate_task_assets, verify_asset
from novacode.evaluation.contracts import (
    DIFFICULTIES,
    SOURCES,
    SPECIALTIES,
    SUITES,
    Asset,
    EvaluationTask,
    fingerprint,
    json_text,
    object_data,
    require,
    text,
    version,
)


def validate_corpus(tasks: tuple[EvaluationTask, ...]) -> None:
    ids: set[str] = set()
    candidates: set[tuple[str, str]] = set()
    content: set[tuple[str, str, str, str]] = set()
    families: dict[str, str] = {}
    problems: dict[str, str] = {}
    for task in tasks:
        require(task.task_id not in ids, f"重复任务 ID: {task.task_id}")
        ids.add(task.task_id)
        candidate = task.source, task.candidate_id
        require(candidate not in candidates, f"重复候选: {candidate}")
        candidates.add(candidate)
        identity = (
            task.repository,
            task.base_commit,
            task.initial_state.sha256,
            task.public_input.sha256,
        )
        require(identity not in content, "同一候选内容不得换名重复入库")
        content.add(identity)
        require(families.get(task.family_id, task.suite) == task.suite, "问题家族跨开发/冻结集")
        families[task.family_id] = task.suite
        for declared_key in task.problem_keys:
            key = declared_key.casefold().rstrip("/")
            require(problems.get(key, task.suite) == task.suite, f"同源问题跨集: {key}")
            problems[key] = task.suite
        # builder_id 仅是构建实现身份；共享构建器不表示共享被测问题。


@dataclass(frozen=True, slots=True, kw_only=True)
class Qualification:
    task_sha256: str
    environment_verified: bool
    resources_verified: bool
    original_target_failed: bool
    original_regressions_passed: bool
    reference_passes: tuple[bool, ...]
    correct_implementation_passed: bool
    negative_controls_passed: bool
    evidence: tuple[Asset, ...]
    schema_version: int = 1

    def validate(self, task: EvaluationTask, root: Path) -> None:
        version(self.schema_version)
        require(self.task_sha256 == fingerprint(task), "资格证据绑定了另一份合同")
        flags = (
            self.environment_verified,
            self.resources_verified,
            self.original_target_failed,
            self.original_regressions_passed,
            self.correct_implementation_passed,
            self.negative_controls_passed,
            *self.reference_passes,
        )
        require(all(type(flag) is bool for flag in flags), "资格结果必须为布尔值")
        require(self.environment_verified and self.resources_verified, "环境与资源未核验")
        require(bool(self.evidence), "资格核验缺少外部证据资产")
        for asset in self.evidence:
            verify_asset(asset, root)
        if task.source == "live":
            require(
                self.original_target_failed
                and self.original_regressions_passed
                and self.reference_passes == (True, True, True),
                "Live 必须原始失败且参考连续三次通过",
            )
        else:
            require(
                self.correct_implementation_passed and self.negative_controls_passed,
                "专项必须核验正确实现与负例",
            )

    @classmethod
    def from_json(cls, raw: str) -> Qualification:
        data = object_data(raw)
        data["reference_passes"] = tuple(data["reference_passes"])
        data["evidence"] = tuple(Asset(**asset) for asset in data["evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectionEvent:
    sequence: int
    task_id: str
    task_sha256: str
    action: str
    reason: str
    note: str
    evidence: Asset | None = None
    replacement_id: str | None = None
    schema_version: int = 1

    @classmethod
    def from_json(cls, raw: str) -> SelectionEvent:
        data = object_data(raw)
        if data.get("evidence") is not None:
            data["evidence"] = Asset(**data["evidence"])
        return cls(**data)


REASONS = {
    "SELECT": {"selection-rule"},
    "ADMIT": {"qualification-passed"},
    "FREEZE": {"campaign-frozen"},
    "EXCLUDE": {"environment-invalid", "contract-invalid", "family-conflict", "quota-rule"},
    "REPLACE": {"environment-invalid", "contract-invalid", "family-conflict", "quota-rule"},
}


class SelectionLedger:
    """按合同指纹重放所有事件；首版约定单写入者，拒绝截断尾行。"""

    def __init__(
        self,
        path: Path,
        tasks: tuple[EvaluationTask, ...],
        public_root: Path,
        evidence_root: Path,
        budget_categories: frozenset[str],
    ) -> None:
        validate_corpus(tasks)
        self.path = path
        self.tasks = {task.task_id: task for task in tasks}
        self.public_root = public_root
        self.evidence_root = evidence_root
        self.budget_categories = budget_categories

    def replay(self) -> tuple[dict[str, str], tuple[SelectionEvent, ...]]:
        states = {task_id: "CANDIDATE" for task_id in self.tasks}
        events: list[SelectionEvent] = []
        raw = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        require(not raw or raw.endswith("\n"), "选择账本尾行不完整，必须先保留并处理中断证据")
        for line in raw.splitlines():
            event = SelectionEvent.from_json(line)
            require(
                type(event.sequence) is int and event.sequence == len(events) + 1, "账本序号不连续"
            )
            self._apply(states, event)
            events.append(event)
        return states, tuple(events)

    def _apply(self, states: dict[str, str], event: SelectionEvent) -> None:
        version(event.schema_version)
        require(event.task_id in self.tasks, "未知任务")
        task = self.tasks[event.task_id]
        require(event.task_sha256 == fingerprint(task), "账本任务合同已改变")
        require(
            event.reason in REASONS.get(event.action, set()), "选择原因不合法，禁止依据 Agent 成败"
        )
        text(event.note, "选择/排除说明")
        require(
            event.action == "REPLACE" or event.replacement_id is None, "只有替换事件可指定替代题"
        )
        current = states[event.task_id]
        transitions = {
            "SELECT": {"CANDIDATE"},
            "ADMIT": {"SELECTED"},
            "FREEZE": {"ADMITTED"},
            "EXCLUDE": {"CANDIDATE", "SELECTED", "ADMITTED"},
            "REPLACE": {"SELECTED", "ADMITTED"},
        }
        require(
            current in transitions[event.action], f"非法题库状态转换: {current} -> {event.action}"
        )
        if event.action in ("ADMIT", "FREEZE"):
            validate_task_assets(task, self.public_root, self.evidence_root, self.budget_categories)
            require(event.evidence is not None, "缺少入库/冻结证据")
            assert event.evidence is not None
            path = verify_asset(event.evidence, self.evidence_root)
            if event.action == "ADMIT":
                Qualification.from_json(path.read_text(encoding="utf-8")).validate(
                    task, self.evidence_root
                )
            else:
                freeze = object_data(path.read_text(encoding="utf-8"))
                require(
                    freeze.get("schema_version") == 1
                    and freeze.get("task_sha256") == fingerprint(task)
                    and freeze.get("suite") == task.suite == "frozen",
                    "冻结证据不属于该冻结任务",
                )
        if event.action == "REPLACE":
            self._replace(states, task, event)
        else:
            states[event.task_id] = {
                "SELECT": "SELECTED",
                "ADMIT": "ADMITTED",
                "FREEZE": "FROZEN",
                "EXCLUDE": "EXCLUDED",
            }[event.action]

    def _replace(self, states: dict[str, str], task: EvaluationTask, event: SelectionEvent) -> None:
        require(event.replacement_id in self.tasks, "替代题不存在")
        assert event.replacement_id is not None
        replacement = self.tasks[event.replacement_id]
        require(states[replacement.task_id] == "CANDIDATE", "替代题必须为未使用候选")
        require(
            (task.suite, task.source, task.difficulty, task.category)
            == (
                replacement.suite,
                replacement.source,
                replacement.difficulty,
                replacement.category,
            ),
            "替代题必须保持来源/题集/难度/主类别配额",
        )
        states[task.task_id] = "REPLACED"
        states[replacement.task_id] = "SELECTED"

    def append(
        self,
        task_id: str,
        action: str,
        reason: str,
        note: str,
        *,
        evidence: Asset | None = None,
        replacement_id: str | None = None,
    ) -> SelectionEvent:
        states, events = self.replay()
        require(task_id in self.tasks, "未知任务")
        event = SelectionEvent(
            sequence=len(events) + 1,
            task_id=task_id,
            task_sha256=fingerprint(self.tasks[task_id]),
            action=action,
            reason=reason,
            note=note,
            evidence=evidence,
            replacement_id=replacement_id,
        )
        self._apply(states, event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event


def quota_report(tasks: tuple[EvaluationTask, ...], states: dict[str, str]) -> dict[str, Any]:
    validate_corpus(tasks)
    require(set(states) == {task.task_id for task in tasks}, "配额状态与候选集合不一致")
    require(
        set(states.values())
        <= {"CANDIDATE", "SELECTED", "ADMITTED", "FROZEN", "EXCLUDED", "REPLACED"},
        "未知入库状态",
    )
    rows = []
    for suite in SUITES:
        for source in SOURCES:
            members = [task for task in tasks if task.suite == suite and task.source == source]
            admitted = [task for task in members if states[task.task_id] in ("ADMITTED", "FROZEN")]
            factor = 1 if suite == "development" else 2
            difficulty = Counter(task.difficulty for task in admitted)
            categories = Counter(task.category for task in admitted)
            targets = dict(zip(DIFFICULTIES, (4 * factor, 6 * factor, 2 * factor), strict=True))
            category_targets = (
                dict(
                    zip(
                        SPECIALTIES,
                        (2 * factor, 2 * factor, 2 * factor, 3 * factor, 3 * factor),
                        strict=True,
                    )
                )
                if source == "specialty"
                else {}
            )
            require(
                all(difficulty[key] <= target for key, target in targets.items()), "难度配额超额"
            )
            require(
                all(categories[key] <= target for key, target in category_targets.items()),
                "专项配额超额",
            )
            rows.append(
                {
                    "suite": suite,
                    "source": source,
                    "candidates": len(members),
                    "admitted": len(admitted),
                    "frozen": sum(states[t.task_id] == "FROZEN" for t in members),
                    "target": 12 * factor,
                    "gap": 12 * factor - len(admitted),
                    "difficulty": dict(difficulty),
                    "difficulty_targets": targets,
                    "difficulty_gaps": {
                        key: target - difficulty[key] for key, target in targets.items()
                    },
                    "categories": dict(categories),
                    "category_targets": category_targets,
                    "category_gaps": {
                        key: target - categories[key] for key, target in category_targets.items()
                    },
                }
            )
    return {"schema_version": 1, "rows": rows}
