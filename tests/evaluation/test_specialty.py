"""专项正确/错误对照、确定性故障和真实 MCP 的机制验收。"""

import copy
import json
import os
import sys
from pathlib import Path

import pytest

from novacode.evaluation.ledger import Ledger, rebuild
from novacode.evaluation.specialty_tools import (
    STANDARD_TEST,
    FirstFault,
    RecordTool,
    connect_records,
    registry_for,
)
from novacode.tool import with_cwd
from novacode.tool.bash import BashTool
from novacode.tool.grep_tool import GrepTool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation/specialty"))
from definitions import cases  # noqa: E402
from judge import facts_pass, grade  # noqa: E402


def manifest(case):
    return {
        "task_id": case.task_id,
        "initial_files": case.files,
        "answers": case.answers,
        "mutants": case.mutants,
        "checks": case.checks,
        "allowed_paths": case.allowed,
        "image": "trusted-local-only",
        "conditions": (
            ["correct-tests", "all-mutants", "scope"]
            if case.mutants
            else (
                ["facts", "behavior", "scope"]
                if case.answers and case.checks
                else ["facts", "scope"]
                if case.answers
                else ["behavior", "scope"]
            )
        ),
    }


@pytest.mark.parametrize("case", cases(), ids=lambda case: case.task_id)
def test_reference_and_negative(case):
    data = manifest(case)
    answer = json.dumps(case.answers[0]) if case.answers else ""
    good = grade(data, case.reference, answer, local=True)
    assert all(good["conditions"].values()), good
    wrong = grade(data, {}, "{}", local=True)
    assert not all(wrong["conditions"].values()), wrong
    changes = {**case.reference, "unauthorized.txt": "changed"}
    assert not grade(data, changes, answer, local=True)["conditions"]["scope"]


@pytest.mark.parametrize("task_id", ["S01", "S02"])
def test_alternative_paths_and_wrong_relation(task_id):
    case = next(c for c in cases() if c.task_id == task_id)
    for answer in case.answers:
        assert facts_pass(json.dumps(answer), case.answers)
        explained = json.dumps(answer) + '\n解释中的配置示例：{"mode":"safe","label":"default"}'
        assert facts_pass(explained, case.answers)
        normalized = copy.deepcopy(answer)
        normalized["files"] = ["./" + name for name in answer["files"]]
        assert facts_pass(json.dumps(normalized), case.answers)
    wrong = copy.deepcopy(case.answers[0])
    wrong["relations"][0].reverse()
    assert not facts_pass(json.dumps(wrong), case.answers)
    assert not facts_pass(json.dumps(case.answers[0]) + json.dumps(wrong), case.answers)


@pytest.mark.parametrize("task_id", ["S03", "S04"])
@pytest.mark.parametrize(
    "body",
    [
        "import absent_dependency\n",
        (
            "import unittest\n@unittest.skip('skip')\nclass Tests(unit"
            "test.TestCase):\n    def test_skip(self): pass\n"
        ),
        (
            "import unittest\nclass Tests(unittest.TestCase):\n    def"
            " test_fail(self): self.fail('unconditional')\n"
        ),
        (
            "import unittest\nclass Tests(unittest.TestCase):\n    def"
            " test_import(self): import settings\n"
        ),
    ],
)
def test_invalid_added_tests_fail(task_id, body):
    case = next(c for c in cases() if c.task_id == task_id)
    result = grade(manifest(case), {"tests/test_added.py": body}, "", local=True)
    assert not all(result["conditions"].values()), result


@pytest.mark.parametrize("repeat", range(5))
def test_cancellation_is_repeatable(repeat):
    case = cases()[3]
    result = grade(manifest(case), case.reference, "", local=True)
    assert all(result["conditions"].values()), (repeat, result)


def test_multistage_does_not_accept_erased_or_missing_early_work():
    case = cases()[8]
    for path in ("config.py", "client.py"):
        broken = {**case.reference, path: case.files[path]}
        assert not grade(manifest(case), broken, "", local=True)["conditions"]["behavior"]
    for case in cases()[6:9]:
        assert len(case.requests) > 1
        assert "summary" not in json.dumps(case.requests)
        assert "kind=counter" not in case.requests[-1]
        assert "retry_delay=17" not in case.requests[-1]


async def test_first_search_fault_and_zero_coverage(tmp_path):
    (tmp_path / "bounds.py").write_text("def clamp(): pass\n")
    ledger = Ledger(tmp_path / "ledger.jsonl", "fault-grep")
    fault = FirstFault(GrepTool(), "S05", ledger.append)
    with with_cwd(str(tmp_path)):
        assert (await fault.execute('{"pattern":"["}')).is_error
        assert not fault.triggered
        assert rebuild(ledger.path)["fault_injections"] == 0
        assert (await fault.execute('{"pattern":"clamp"}')).is_error
        assert not (await fault.execute('{"pattern":"clamp"}')).is_error
    records = rebuild(ledger.path)
    ledger.close()
    assert records["fault_injections"] == 1


async def test_actual_timeout_cleans_child_and_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_ok.py").write_text(
        "import unittest\nclass Test(unittest.TestCase):\n    def "
        "test_ok(self): self.assertTrue(True)\n"
    )
    ledger = Ledger(tmp_path / "ledger.jsonl", "fault-bash")
    fault = FirstFault(BashTool(), "S06", ledger.append)
    with with_cwd(str(tmp_path)):
        args = json.dumps({"command": STANDARD_TEST})
        assert (await fault.execute(args)).is_error
        second = await fault.execute(args)
        assert not second.is_error and "exit_code: 0" in second.content
    assert rebuild(ledger.path)["fault_injections"] == 1
    assert rebuild(ledger.path)["fault_cleanup_failed"] == 0
    ledger.close()


@pytest.mark.parametrize("task_id", ["S10", "S11", "S12"])
async def test_builtin_and_real_mcp_match_public_records(tmp_path, task_id):
    case = next(c for c in cases() if c.task_id == task_id)
    records = tmp_path / "records.json"
    records.write_text(json.dumps(case.records))
    registry = registry_for(task_id, case.records, lambda *a, **kw: None)
    manager = await connect_records(task_id, records)
    try:
        if manager:
            for tool in manager.tools():
                registry.register(tool)
        for name, rows in case.records.items():
            tool = next(
                (
                    registry.get(t.name)
                    for t in registry.definitions()
                    if t.name == name or t.name.endswith("__" + name)
                ),
                None,
            )
            assert tool is not None, [t.name for t in registry.definitions()]
            for key, row in rows.items():
                actual = await tool.execute(json.dumps({"key": key}))
                assert not actual.is_error
                # MCP adapter 使用 text content；其数据与同业务内置路径相同。
                expected = await RecordTool(name, rows).execute(json.dumps({"key": key}))
                assert expected.content in actual.content
    finally:
        if manager:
            await manager.close()
            assert all(task.done() for task in manager._tasks)


@pytest.mark.parametrize("task_id", ["S07", "S08", "S09"])
async def test_multiturn_worker_counts_all_stages(tmp_path, monkeypatch, task_id):
    from dataclasses import asdict, replace

    from novacode.evaluation.worker import execute
    from novacode.llm import ToolCall
    from tests.evaluation.test_runner import LIMITS, Scripted, payload

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("public")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    case = next(case for case in cases() if case.task_id == task_id)
    data = payload()
    data["requests"] = case.requests
    data["limits"] = data["total_limits"] = asdict(replace(LIMITS, provider_calls=30))
    provider = Scripted(ToolCall("read", "read_file", '{"path":"value.txt"}'))
    result = await execute(data, root, output, provider=provider)
    assert result["completed_requests"] == len(case.requests)
    assert len(result["agent_run_ids"]) == len(case.requests)
    assert result["metrics"]["provider_calls"] == len(case.requests) + 1
    assert result["metrics"]["measured_tokens"] == 120 * (len(case.requests) + 1)
    assert provider.closes == 1


@pytest.mark.parametrize("config_id", ["full", "no-compression", "eager-schema"])
async def test_record_data_and_discovery_cost_in_all_modes(tmp_path, monkeypatch, config_id):
    from types import SimpleNamespace

    from novacode.evaluation.worker import execute
    from novacode.llm import StreamEvent, ToolCall
    from novacode.llm.anthropic_provider import _usage_from_anthropic
    from tests.evaluation.test_runner import Scripted, payload

    case = cases()[10]
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "records.json").write_text(json.dumps(case.records))
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    target = "mcp__specialty__ci_test"
    row = case.records["ci_test"]["case-884"]

    class RecordProvider(Scripted):
        async def stream(self, req):
            self.calls += 1
            self.seen.append(req)
            usage = _usage_from_anthropic(SimpleNamespace(input_tokens=100, output_tokens=20))
            calls = {
                1: ToolCall("discover", "discover_tools", json.dumps({"query": target})),
                2: ToolCall("query", target, '{"key":"case-884"}'),
            }
            if self.calls in calls:
                yield StreamEvent(tool_calls=[calls[self.calls]], usage=usage, done=True)
            else:
                results = [
                    r for m in req.messages for r in m.tool_results if r.tool_call_id == "query"
                ]
                assert len(results) == 1 and not results[0].is_error
                assert json.loads(results[0].content) == row
                yield StreamEvent(text=json.dumps(row), usage=usage, done=True)

    data = payload()
    data.update(
        config_id=config_id,
        requests=case.requests,
        specialty={"task_id": "S11", "tool_records": case.records},
    )
    data["allowed_tools"] += [target, "mcp__specialty__ci_deploy"]
    provider = RecordProvider()
    result = await execute(data, root, output, provider=provider)
    assert result["termination"] == "completed", result
    assert result["cleanup"] == "passed"
    assert result["metrics"]["measured_tokens"] == 360
    assert result["metrics"]["discovery_calls"] == 1
    assert result["metrics"]["tool_calls"] == 2
    initial = {t.name for t in provider.seen[0].tools}
    assert (target in initial) == (config_id == "eager-schema")
    assert target in {t.name for t in provider.seen[1].tools}


def test_valid_alternative_fix_is_accepted():
    case = cases()[4]
    alternative = (
        "def clamp(value, lower, upper):\n"
        '    if lower > upper: raise ValueError("reversed")\n'
        "    if value < lower: return lower\n"
        "    if value > upper: return upper\n"
        "    return value\n"
    )
    assert all(
        grade(manifest(case), {"bounds.py": alternative}, "", local=True)["conditions"].values()
    )
