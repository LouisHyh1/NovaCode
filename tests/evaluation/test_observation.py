"""第三阶段机制验收：正式 Agent、摘要、SDK HTTP、并发工具和账本。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from novacode.agent import Agent
from novacode.agent.tool_runner import ToolRunner
from novacode.compact.layer2 import run_summary, summarize_once
from novacode.compact.state import RecoveryState
from novacode.config import ConfigError, ProviderConfig, load
from novacode.conversation import Conversation
from novacode.evaluation.budget import BudgetExceededError, BudgetLimits, BudgetPolicy
from novacode.evaluation.ledger import Ledger, read_records, rebuild
from novacode.evaluation.observation import ObservedProvider
from novacode.evaluation.usage import UsageRule, normalize
from novacode.hook import DispatchResult
from novacode.hook import Event as HookEvent
from novacode.llm import Message, PromptTooLongError, Request, StreamEvent, ToolCall
from novacode.llm.anthropic_provider import AnthropicProvider, _usage_from_anthropic
from novacode.llm.openai_provider import OpenAIProvider, _usage_from_openai
from novacode.permission import Decision, Mode
from novacode.tool import Registry, Result

LIMITS = BudgetLimits(
    seconds=60,
    tokens=100_000,
    provider_calls=10,
    tool_calls=10,
    single_request_tokens=20_000,
    cleanup_seconds=5,
)
RULE = UsageRule("anthropic-cache-separate-v1", "anthropic", "separate", "a" * 64)


def usage(input_tokens=100, output_tokens=20):
    return _usage_from_anthropic(
        SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=30,
        )
    )


class ScriptedProvider:
    name = "mechanism-test"
    model = "no-real-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.close_count = 0

    def request_metadata(self, req):
        return {
            "sdk_max_retries": 0,
            "timeout_seconds": 10,
            "max_output_tokens": 500,
            "protocol": "anthropic",
            "sdk_version": "scripted",
        }

    async def stream(self, req):
        self.requests.append(req)
        for event in next(self.responses):
            if isinstance(event, BaseException):
                raise event
            yield event

    async def close(self):
        self.close_count += 1


def observed(tmp_path, responses, *, limits=LIMITS, rule=RULE):
    ledger = Ledger(tmp_path / "trace.jsonl", "run-1", secrets=("private-key",))
    policy = BudgetPolicy(total=LIMITS, categories={"small": limits})
    provider = ScriptedProvider(responses)
    wrapper = ObservedProvider(provider, ledger, policy.for_run("small", "full"), usage_rule=rule)
    return wrapper, provider, ledger


async def drain(provider, req=None):
    return [event async for event in provider.stream(req or Request())]


def events(ledger, kind):
    return [row["data"] for row in read_records(ledger.path)[0] if row["kind"] == kind]


async def test_main_and_summary_ignored_usage_are_counted_once(tmp_path):
    u = usage()
    wrapper, provider, ledger = observed(
        tmp_path,
        [
            [StreamEvent(text="main", usage=u), StreamEvent(usage=u, done=True)],
            [StreamEvent(text="<summary>accepted</summary>", usage=u), StreamEvent(done=True)],
        ],
    )
    await drain(wrapper)
    result = await summarize_once(
        SimpleNamespace(provider=wrapper.borrow()), [Message("user", "x")]
    )
    assert result == "accepted"
    starts = events(ledger, "request_start")
    assert [row["role"] for row in starts] == ["main", "summary"]
    assert len({row["request_id"] for row in starts}) == 2
    assert rebuild(ledger.path)["measured_tokens"] == 320
    await asyncio.gather(wrapper.borrow().close(), wrapper.borrow().close())
    assert provider.close_count == 0
    await asyncio.gather(wrapper.close(), wrapper.close())
    assert provider.close_count == 1
    ledger.close()


async def test_summary_retry_has_explicit_role_and_parent(tmp_path):
    wrapper, _, ledger = observed(
        tmp_path,
        [
            [StreamEvent(usage=usage()), StreamEvent(err=PromptTooLongError("too long"))],
            [
                StreamEvent(text="<summary>retry works</summary>", usage=usage()),
                StreamEvent(done=True),
            ],
        ],
    )
    conv = Conversation.from_messages([Message("user", "first"), Message("user", "second")])
    result = await run_summary(
        SimpleNamespace(
            provider=wrapper.borrow(),
            conv=conv,
            recovery=RecoveryState(),
            tool_defs=[],
        )
    )
    assert "retry works" in result[0].content
    starts = events(ledger, "request_start")
    assert starts[1]["retry_of"] == starts[0]["request_id"]
    assert starts[0]["logical_call_id"] == starts[1]["logical_call_id"]
    assert [row["attempt"] for row in starts] == [1, 2]
    assert all(row["role"] == "summary" for row in starts)
    assert rebuild(ledger.path)["measured_tokens"] == 320
    await wrapper.close()
    ledger.close()


@pytest.mark.parametrize("error_event", [False, True, "factory"])
async def test_stream_failure_unknown_usage_preserves_error_and_stops(tmp_path, error_event):
    failure = ConnectionError("private-key authorization failure")
    wrapper, provider, ledger = observed(
        tmp_path,
        [
            [StreamEvent(text="partial"), StreamEvent(err=failure) if error_event else failure],
        ],
    )
    if error_event == "factory":

        def stream(req):
            provider.requests.append(req)
            raise failure

        provider.stream = stream
    if error_event is True:
        assert (await drain(wrapper))[-1].err is failure
    else:
        with pytest.raises(ConnectionError) as caught:
            await drain(wrapper)
        assert caught.value is failure
    assert rebuild(ledger.path)["measured_tokens"] is None
    assert rebuild(ledger.path)["unknown_usage_requests"] == 1
    with pytest.raises(BudgetExceededError, match="unknown-usage"):
        await drain(wrapper)
    assert len(provider.requests) == 1
    assert "private-key" not in ledger.path.read_text()
    await wrapper.close()
    ledger.close()


async def test_partial_consumer_close_is_observable(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [[StreamEvent(text="partial"), StreamEvent(done=True)]])
    stream = wrapper.stream(Request())
    await anext(stream)
    await stream.aclose()
    assert events(ledger, "request_end")[0]["status"] == "consumer-closed"
    assert rebuild(ledger.path)["measured_tokens"] is None
    await wrapper.close()
    ledger.close()


async def test_usage_is_durable_before_request_end_and_not_added_twice(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [[StreamEvent(usage=usage()), StreamEvent(done=True)]])
    stream = wrapper.stream(Request())
    await anext(stream)
    assert not events(ledger, "request_end")
    assert rebuild(ledger.path)["provider_calls"] == 1
    assert rebuild(ledger.path)["measured_tokens"] == 160
    await stream.aclose()
    assert rebuild(ledger.path)["measured_tokens"] == 160
    await wrapper.close()
    ledger.close()


@pytest.mark.parametrize(
    "protocol,cache_mode,expected",
    [
        ("anthropic", "separate", 160),
        ("anthropic", "subset", 120),
        ("openai", "subset", 120),
    ],
)
def test_normalization_verified_cache_and_reasoning_subsets(protocol, cache_mode, expected):
    u = (
        usage()
        if protocol == "anthropic"
        else _usage_from_openai(
            SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                prompt_tokens_details=SimpleNamespace(cached_tokens=40),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=10),
            )
        )
    )
    rule = UsageRule(u.normalization, protocol, cache_mode, "b" * 64)
    result = normalize(u, rule)
    assert result["measured_total"] == expected
    assert result["raw"] == u.raw
    assert normalize(u, None)["measured_total"] is None


def test_missing_usage_fields_are_partial_not_zero():
    u = usage()
    u.raw["output_tokens"] = None
    result = normalize(u, RULE)
    assert result["measured_total"] is None
    assert result["known_lower_bound"] == 140
    assert result["completeness"] == "partial"
    assert normalize(None, RULE)["completeness"] == "unknown"


@pytest.mark.parametrize(
    "provider_type,protocol",
    [
        (AnthropicProvider, "anthropic"),
        (OpenAIProvider, "openai"),
    ],
)
async def test_real_sdk_500_has_one_http_attempt_with_zero_retries(provider_type, protocol):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "failure"}})

    cfg = ProviderConfig("test", protocol, "unused-secret", "model", max_retries=0, timeout=2)
    provider = provider_type(cfg)
    await provider._client._client.aclose()
    provider._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    result = await drain(provider, Request(messages=[Message("user", "hello")]))
    assert len(requests) == 1
    assert result[-1].err is not None
    metadata = provider.request_metadata(Request())
    assert metadata["sdk_max_retries"] == 0
    assert metadata["timeout_seconds"] == 2
    assert metadata["sdk_version"]
    assert "unused-secret" not in json.dumps(metadata)
    await provider.close()


@pytest.mark.parametrize(
    "provider_type,protocol",
    [
        (AnthropicProvider, "anthropic"),
        (OpenAIProvider, "openai"),
    ],
)
async def test_ordinary_sdk_defaults_preserved(provider_type, protocol):
    provider = provider_type(ProviderConfig("test", protocol, "unused", "model"))
    assert provider._client.max_retries == 2
    assert provider.request_metadata(Request())["sdk_retry_rule"] == "sdk-default"
    await provider.close()


async def test_anthropic_actual_thinking_condition_matches_http_request():
    bodies = []

    async def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "failure"}})

    provider = AnthropicProvider(
        ProviderConfig(
            "test",
            "anthropic",
            "unused",
            "model",
            thinking=True,
            max_retries=0,
            timeout=2,
        )
    )
    await provider._client._client.aclose()
    provider._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    plain = Request(messages=[Message("user", "hello")])
    history = Request(
        messages=[Message("assistant", tool_calls=[ToolCall("c", "read_file", "{}")])]
    )
    await drain(provider, plain)
    await drain(provider, history)
    assert bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "thinking" not in bodies[1]
    assert all(body["max_tokens"] == 4096 for body in bodies)
    assert provider.request_metadata(plain)["thinking_sent"] is True
    assert provider.request_metadata(history)["thinking_sent"] is False
    await provider.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_retries", -1),
        ("max_retries", True),
        ("timeout", 0),
        ("timeout", ".inf"),
        ("max_output_tokens", 0),
    ],
)
def test_invalid_sdk_config_rejected(tmp_path, field, value):
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n  - name: test\n    protocol: anthropic\n    api_key: unused\n"
        f"    model: model\n    {field}: {value}\n"
    )
    with pytest.raises(ConfigError):
        load(str(path))


@pytest.mark.parametrize("missing", ["sdk_max_retries", "timeout_seconds", "max_output_tokens"])
async def test_missing_request_parameter_prevents_execution(tmp_path, missing):
    wrapper, provider, ledger = observed(tmp_path, [])
    original = provider.request_metadata
    provider.request_metadata = lambda req: {k: v for k, v in original(req).items() if k != missing}
    with pytest.raises(ValueError):
        await drain(wrapper)
    assert provider.requests == []
    assert rebuild(ledger.path)["provider_calls"] == 0
    await wrapper.close()
    ledger.close()


async def dispatch(*args, **kwargs):
    return DispatchResult()


class AuditTool:
    read_only = True

    def __init__(self, name="audit"):
        self._name = name
        self.active = 0
        self.max_active = 0
        self.executed = 0

    def name(self):
        return self._name

    def description(self):
        return "Audit mechanism"

    def parameters(self):
        return {"type": "object"}

    async def execute(self, args):
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        self.executed += 1
        await asyncio.sleep(0)
        self.active -= 1
        return Result(args, is_error="fail" in args)


async def collect_tools(runner, calls, *, mode=Mode.DEFAULT):
    return [u async for u in runner.run(calls, Conversation(), asyncio.Event(), mode)]


async def test_same_name_concurrent_tools_full_arguments_and_retry_counts(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [])
    tool = AuditTool()
    registry = Registry()
    registry.register(tool)
    registry.register(AuditTool("discover_tools"))
    runner = ToolRunner(registry, dispatch_hook=dispatch, observer=wrapper.tool_observer)
    long = "business-value" * 30
    calls = [
        ToolCall("one", "audit", json.dumps({"value": long, "api_key": "private-key"})),
        ToolCall("two", "audit", '{"fail":true}'),
    ]
    updates = await collect_tools(runner, calls)
    assert tool.max_active == 2
    assert updates[-1].result.results[0].tool_call_id == "one"
    await collect_tools(
        runner,
        [ToolCall("retry", "audit", '{"fail":true}'), ToolCall("discover", "discover_tools", "{}")],
    )
    starts, ends = events(ledger, "tool_start"), events(ledger, "tool_end")
    assert starts[0]["call_id"] == "one" and starts[1]["call_id"] == "two"
    assert len(starts[0]["full_args"]) > 80 and long in starts[0]["full_args"]
    assert [s["invocation_id"] for s in starts] == [e["invocation_id"] for e in ends]
    assert [s["sequence"] for s in starts] == [1, 2, 3, 4]
    assert all(e["ended_monotonic"] >= e["started_monotonic"] for e in ends)
    metrics = rebuild(ledger.path)
    assert metrics["tool_calls"] == metrics["tool_executed"] == 4
    assert metrics["discovery_calls"] == metrics["tool_retries"] == 1
    assert metrics["tool_errors"] == 2 and metrics["tool_succeeded"] == 2
    assert starts[2]["retry_of"] == starts[1]["invocation_id"]
    assert "private-key" not in ledger.path.read_text()
    for output in (ledger.path.parent / "artifacts").iterdir():
        assert "private-key" not in output.read_text()
    await wrapper.close()
    ledger.close()


async def test_denied_unknown_and_hook_blocked_tools_not_executed(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [])
    registry = Registry()
    tool = AuditTool()
    registry.register(tool)
    engine = SimpleNamespace(check=lambda *args: (Decision.DENY, "denied"))
    runner = ToolRunner(
        registry, engine=engine, dispatch_hook=dispatch, observer=wrapper.tool_observer
    )
    await collect_tools(runner, [ToolCall("deny", "audit", "{}")])
    runner = ToolRunner(registry, dispatch_hook=dispatch, observer=wrapper.tool_observer)
    await collect_tools(runner, [ToolCall("unknown", "absent", "{}")])

    async def blocked(event, *args, **kwargs):
        return DispatchResult(blocked=event == HookEvent.PRE_TOOL_USE, reason="hook-denied")

    runner = ToolRunner(registry, dispatch_hook=blocked, observer=wrapper.tool_observer)
    await collect_tools(runner, [ToolCall("blocked", "audit", "{}")])
    assert tool.executed == 0
    ends = events(ledger, "tool_end")
    assert ends[0]["authorization_status"] == "denied"
    assert ends[1]["error_type"] == "UnknownTool"
    assert ends[2]["authorization_status"] == "denied"
    assert rebuild(ledger.path)["tool_calls"] == 3
    assert rebuild(ledger.path)["tool_denied"] == 2
    assert rebuild(ledger.path)["tool_executed"] == 0
    await wrapper.close()
    ledger.close()


async def test_budget_includes_summary_discovery_denial_and_total(tmp_path):
    wrapper, _, ledger = observed(
        tmp_path,
        [
            [StreamEvent(usage=usage(), done=True)],
            [StreamEvent(text="<summary>x</summary>", usage=usage(), done=True)],
        ],
        limits=replace(LIMITS, provider_calls=2, tool_calls=1),
    )
    await drain(wrapper)
    await summarize_once(SimpleNamespace(provider=wrapper.borrow()), [])
    with pytest.raises(BudgetExceededError, match="provider-call-budget"):
        await drain(wrapper)
    registry = Registry()
    registry.register(AuditTool("discover_tools"))
    runner = ToolRunner(registry, dispatch_hook=dispatch, observer=wrapper.tool_observer)
    await collect_tools(runner, [ToolCall("discover", "discover_tools", "{}")])
    with pytest.raises(BudgetExceededError, match="tool-call-budget"):
        await collect_tools(runner, [ToolCall("deny", "missing", "{}")])
    assert rebuild(ledger.path)["provider_calls"] == 2
    assert rebuild(ledger.path)["tool_calls"] == 1
    await wrapper.close()
    ledger.close()


def test_category_temporary_total_and_pending_reservations_have_same_limits():
    category = replace(LIMITS, tokens=1000, single_request_tokens=800)
    policy = BudgetPolicy(total=category, categories={"small": category}, temporary=category)
    runs = [
        policy.for_run("small", config) for config in ("full", "no-compression", "eager-schema")
    ]
    assert all(run.meters[0].limits == category == run.meters[1].limits for run in runs)
    runs[0].reserve_request(100, 500)
    with pytest.raises(BudgetExceededError, match="token-budget"):
        runs[1].reserve_request(100, 500)
    assert runs[1].meters[0].provider_calls == 0
    runs[0].settle_request(600, {"measured_total": 200, "known_lower_bound": 200})
    runs[1].reserve_request(100, 500)
    with pytest.raises(BudgetExceededError, match="single-request"):
        runs[2].reserve_request(1000, 500)


async def test_actual_overrun_saved_and_stops_next_request(tmp_path):
    wrapper, provider, ledger = observed(tmp_path, [[StreamEvent(usage=usage(30_000), done=True)]])
    await drain(wrapper)
    assert rebuild(ledger.path)["measured_tokens"] == 30_060
    with pytest.raises(BudgetExceededError, match="actual-token-overrun"):
        await drain(wrapper)
    assert len(provider.requests) == 1
    await wrapper.close()
    ledger.close()


@pytest.mark.parametrize(
    "field,value",
    [("tokens", 0), ("seconds", float("nan")), ("cleanup_seconds", None), ("provider_calls", True)],
)
def test_missing_or_invalid_budget_rejected(field, value):
    with pytest.raises(ValueError):
        replace(LIMITS, **{field: value})
    with pytest.raises(TypeError):
        BudgetLimits(seconds=1)


def test_monotonic_boundaries_redaction_artifacts_and_truncated_rebuild(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("novacode.evaluation.ledger.time.monotonic", lambda: clock[0])
    ledger = Ledger(tmp_path / "trace.jsonl", "run", secrets=("private-key",))
    for name, duration in (
        ("preparation", 5),
        ("initialization", 2),
        ("agent", 3),
        ("cleanup", 1),
        ("grading", 4),
    ):
        with ledger.phase(name):
            clock[0] += duration
    ledger.append(
        "auth",
        nested={"authorization": "hidden", "business": "retained"},
        command="curl -H 'Authorization: Bearer private-key'",
        password="hidden",
    )
    artifact = ledger.artifact("business-result private-key")
    ledger.append("answer", output=artifact)
    before = rebuild(ledger.path)
    assert before["main_end_to_end_seconds"] == 6
    assert before["phase_seconds"]["preparation"] == 5
    assert before["phase_seconds"]["grading"] == 4
    assert "private-key" not in ledger.path.read_text() and "hidden" not in ledger.path.read_text()
    assert "retained" in ledger.path.read_text()
    ledger.close()
    original = ledger.path.read_bytes()
    assert rebuild(ledger.path) == before
    assert ledger.path.read_bytes() == original
    with ledger.path.open("ab") as stream:
        stream.write(b'{"interrupted":')
    after = rebuild(ledger.path)
    assert after == {**before, "truncated_tail": True}
    with pytest.raises(FileExistsError):
        Ledger(ledger.path, "run")


async def test_interrupted_request_rebuild_keeps_unknown_and_known_lower_bound(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [[StreamEvent(usage=usage(), done=True)]])
    await drain(wrapper)
    ledger.append("request_start", request_id="unfinished", role="summary")
    result = rebuild(ledger.path)
    assert result["provider_calls"] == 2
    assert result["measured_tokens"] is None
    assert result["known_token_lower_bound"] == 160
    await wrapper.close()
    ledger.close()


async def test_formal_agent_observer_records_tools_without_double_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = Registry()
    registry.register(AuditTool())
    wrapper, _, ledger = observed(
        tmp_path,
        [
            [StreamEvent(tool_calls=[ToolCall("c", "audit", "{}")], usage=usage(), done=True)],
            [StreamEvent(text="done", usage=usage(), done=True)],
        ],
    )
    conv = Conversation()
    conv.add_user("调用 audit 并报告结果")
    agent = Agent(wrapper.borrow(), registry, tool_observer=wrapper.tool_observer)
    assert await agent.run_to_completion(conv, "调用 audit 并报告结果") == "done"
    assert rebuild(ledger.path)["measured_tokens"] == 320
    assert rebuild(ledger.path)["tool_calls"] == 1
    await wrapper.close()
    ledger.close()


async def test_cancellation_preserves_type_and_closes_provider_once(tmp_path):
    started = asyncio.Event()
    wrapper, provider, ledger = observed(tmp_path, [])

    async def blocking(req):
        started.set()
        await asyncio.Event().wait()
        yield StreamEvent(done=True)

    provider.stream = blocking
    task = asyncio.create_task(drain(wrapper))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events(ledger, "request_end")[0]["status"] == "cancelled"
    await wrapper.borrow().close()
    await asyncio.gather(wrapper.close(), wrapper.close())
    assert provider.close_count == 1
    ledger.close()


async def test_budget_deadline_cancels_underlying_stream_and_records_timeout(tmp_path):
    wrapper, provider, ledger = observed(tmp_path, [], limits=replace(LIMITS, seconds=0.03))
    closed = asyncio.Event()

    async def blocking(req):
        try:
            await asyncio.Event().wait()
            yield StreamEvent(done=True)
        finally:
            closed.set()

    provider.stream = blocking
    with pytest.raises(TimeoutError):
        await drain(wrapper)
    assert closed.is_set()
    assert events(ledger, "request_end")[0]["error_type"] == "TimeoutError"
    assert rebuild(ledger.path)["unknown_usage_requests"] == 1
    await wrapper.close()
    ledger.close()


async def test_owner_close_cancellation_and_failure_do_not_duplicate_close(tmp_path):
    wrapper, provider, ledger = observed(tmp_path, [])
    started, finish = asyncio.Event(), asyncio.Event()
    failure = OSError("close failed")

    async def close():
        provider.close_count += 1
        started.set()
        await finish.wait()
        raise failure

    provider.close = close
    task = asyncio.create_task(wrapper.close())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish.set()
    for _ in range(2):
        with pytest.raises(OSError) as caught:
            await wrapper.close()
        assert caught.value is failure
    assert provider.close_count == 1
    assert events(ledger, "provider_close")[0]["status"] == "failed"
    ledger.close()


async def test_original_stream_error_survives_iterator_close_error(tmp_path):
    wrapper, provider, ledger = observed(tmp_path, [])
    original = ConnectionError("original stream error")

    class BrokenIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise original

        async def aclose(self):
            raise OSError("secondary close error")

    provider.stream = lambda req: BrokenIterator()
    with pytest.raises(ConnectionError) as caught:
        await drain(wrapper)
    assert caught.value is original
    assert events(ledger, "stream_close_error")[0]["error_type"] == "OSError"
    await wrapper.close()
    ledger.close()


async def test_cancelled_tool_batch_stops_workers_and_keeps_pairs(tmp_path):
    wrapper, _, ledger = observed(tmp_path, [])
    started, closed = asyncio.Event(), asyncio.Event()

    class SlowTool(AuditTool):
        async def execute(self, args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    registry = Registry()
    registry.register(SlowTool())
    runner = ToolRunner(registry, dispatch_hook=dispatch, observer=wrapper.tool_observer)
    task = asyncio.create_task(collect_tools(runner, [ToolCall("slow", "audit", "{}")]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert rebuild(ledger.path)["incomplete_tools"] == 0
    assert events(ledger, "tool_end")[0]["error_type"] == "CancelledError"
    await wrapper.close()
    ledger.close()


def test_raw_missing_fields_and_separate_reasoning_rule():
    u = _usage_from_anthropic(SimpleNamespace(input_tokens=100, output_tokens=None))
    assert u.raw["output_tokens"] is None
    assert normalize(u, RULE)["measured_total"] is None
    u = usage()
    u.raw["reasoning_tokens"] = 7
    assert normalize(u, replace(RULE, reasoning_mode="separate"))["measured_total"] == 167
    assert normalize(u, RULE)["measured_total"] == 160
    assert (
        normalize(u, replace(RULE, rule_id="fixture-subset-v1", cache_mode="subset"))[
            "measured_total"
        ]
        == 120
    )
    assert normalize(u, replace(RULE, protocol="openai"))["measured_total"] is None


def test_internal_ledger_corruption_rejected_and_missing_phase_unknown(tmp_path):
    ledger = Ledger(tmp_path / "trace.jsonl", "run")
    ledger.append("request_start", request_id="pending")
    output = ledger.artifact('{"api_key": "hidden-key", "business": "kept"}')
    safe = (tmp_path / output["path"]).read_text()
    assert "hidden-key" not in safe and "kept" in safe
    assert rebuild(ledger.path)["phase_seconds"]["preparation"] is None
    ledger.close()
    raw = ledger.path.read_text()
    ledger.path.write_text(raw.replace('"pending"', '"tampered"'))
    with pytest.raises(ValueError, match="指纹"):
        rebuild(ledger.path)


async def test_openai_real_sdk_sse_tool_fragments_usage_and_stream_close():
    chunks = [
        {
            "id": "chat-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "hello",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "audit", "arguments": '{"value":'},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chat-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"ok"}'}}]},
                    "finish_reason": "tool_calls",
                }
            ],
        },
        {
            "id": "chat-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "model",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
        },
    ]
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
    )
    provider = OpenAIProvider(
        ProviderConfig(
            "test",
            "openai",
            "unused",
            "model",
            max_retries=0,
            timeout=2,
            max_output_tokens=500,
        )
    )
    await provider._client._client.aclose()
    provider._client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response)
    )
    result = await drain(provider, Request(messages=[Message("user", "hello")]))
    assert not any(event.err for event in result)
    assert [event.text for event in result if event.text] == ["hello"]
    assert [call for event in result for call in event.tool_calls] == [
        ToolCall("call-1", "audit", '{"value":"ok"}')
    ]
    u = next(event.usage for event in result if event.usage)
    assert u.raw["reasoning_tokens"] == 7 and u.raw["cached_tokens"] == 40
    assert (
        normalize(u, UsageRule(u.normalization, "openai", "subset", "a" * 64))["measured_total"]
        == 120
    )
    assert result[-1].done and response.is_closed
    await provider.close()


async def test_anthropic_real_sdk_sse_raw_cache_fields():
    chunks = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "model": "model",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 30,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hello"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 20},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(f"event: {kind}\ndata: {json.dumps(chunk)}\n\n" for kind, chunk in chunks),
    )
    provider = AnthropicProvider(
        ProviderConfig(
            "test",
            "anthropic",
            "unused",
            "model",
            max_retries=0,
            timeout=2,
        )
    )
    await provider._client._client.aclose()
    provider._client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response)
    )
    result = await drain(provider, Request(messages=[Message("user", "hello")]))
    assert not any(event.err for event in result)
    assert [event.text for event in result if event.text] == ["hello"]
    u = next(event.usage for event in result if event.usage)
    assert u.raw["cache_creation_input_tokens"] == 10 and u.raw["cache_read_input_tokens"] == 30
    assert normalize(u, RULE)["measured_total"] == 160
    assert result[-1].done and response.is_closed
    await provider.close()
