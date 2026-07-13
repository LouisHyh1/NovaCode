from types import SimpleNamespace

from novacode.compact.token import usage_anchor
from novacode.llm.anthropic_provider import _usage_from_anthropic
from novacode.llm.openai_provider import _usage_from_openai


def test_anthropic_context_tokens_include_cache_fields() -> None:
    usage = _usage_from_anthropic(
        SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=40,
        )
    )

    assert usage.context_tokens == 190
    assert usage_anchor(usage) == 190
    assert usage.cache_write == 30
    assert usage.cache_read == 40


def test_openai_context_tokens_do_not_double_count_cached_tokens() -> None:
    usage = _usage_from_openai(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
        )
    )

    assert usage.context_tokens == 120
    assert usage_anchor(usage) == 120
    assert usage.cache_read == 40
