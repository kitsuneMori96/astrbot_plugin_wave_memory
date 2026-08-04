"""LLM provider 回退链与错误分类回归测试。

背景：`tag_llm_provider_id` 单渠道 503 曾让记忆整合自 7-13 起零产出，
而各消费方调用 `build_provider_chain(primary)` 时未传 fallback，链里只有一个元素。
这些测试锁住回退链真的会被遍历，且 402 这类不可自愈错误与 503 被区分。
"""

from __future__ import annotations

import asyncio

import pytest

from services.llm_fallback import (
    build_provider_chain,
    call_first_available_provider,
    describe_provider_error,
    extract_status_code,
    is_retryable_error,
    is_unrecoverable_error,
    parse_provider_ids,
)


class _Resp:
    def __init__(self, text: str):
        self.completion_text = text


class _Provider:
    """按需成功或抛出指定异常的假 provider。"""

    def __init__(self, *, text: str = "", error: Exception | None = None):
        self._text = text
        self._error = error
        self.calls: list[dict] = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _Resp(self._text)


class _Context:
    def __init__(self, providers: dict[str, object]):
        self._providers = providers
        self.lookups: list[str] = []

    def get_provider_by_id(self, provider_id: str):
        self.lookups.append(provider_id)
        return self._providers.get(provider_id)


def _err(message: str) -> Exception:
    return RuntimeError(message)


_BALANCE_402 = (
    "Error code: 402 - {'error': {'message': 'Insufficient Balance', "
    "'type': 'unknown_error', 'code': 'invalid_request_error'}}"
)
_UPSTREAM_503 = (
    "Error code: 503 - {'error': {'message': 'upstream failed', "
    "'type': 'new_api_error', 'code': 'upstream_unavailable'}}"
)


class TestProviderChain:
    def test_primary_only_when_no_fallback(self):
        assert build_provider_chain("grok/grok-4.5") == ["grok/grok-4.5"]

    def test_comma_separated_fallback_is_ordered(self):
        chain = build_provider_chain(
            "grok/grok-4.5",
            "伞云智联/grok-4.5, paidyun/grok-4.5 , gemini/gemini-3.5-flash",
        )
        assert chain == [
            "grok/grok-4.5",
            "伞云智联/grok-4.5",
            "paidyun/grok-4.5",
            "gemini/gemini-3.5-flash",
        ]

    def test_blank_entries_are_dropped(self):
        assert parse_provider_ids(" a , , b ,") == ["a", "b"]
        assert build_provider_chain("", "  ") == []

    def test_list_fallback_accepted(self):
        assert build_provider_chain("a", ["b", "", "c"]) == ["a", "b", "c"]


class TestErrorClassification:
    def test_extracts_status_from_error_code_text(self):
        assert extract_status_code(_err(_BALANCE_402)) == 402
        assert extract_status_code(_err(_UPSTREAM_503)) == 503

    def test_extracts_status_from_attribute(self):
        exc = _err("boom")
        exc.status_code = 429
        assert extract_status_code(exc) == 429

    def test_402_is_unrecoverable_not_retryable(self):
        exc = _err(_BALANCE_402)
        assert is_unrecoverable_error(exc) is True
        assert is_retryable_error(exc) is False

    def test_503_is_retryable_not_unrecoverable(self):
        exc = _err(_UPSTREAM_503)
        assert is_unrecoverable_error(exc) is False
        assert is_retryable_error(exc) is True

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_auth_and_missing_model_are_unrecoverable(self, status):
        assert is_unrecoverable_error(_err(f"Error code: {status} - nope")) is True

    @pytest.mark.parametrize("status", [429, 500, 502, 504])
    def test_transient_statuses_are_retryable(self, status):
        assert is_retryable_error(_err(f"Error code: {status} - busy")) is True

    def test_balance_hint_without_status_still_unrecoverable(self):
        assert is_unrecoverable_error(_err("Insufficient Balance")) is True

    def test_timeout_without_status_is_retryable(self):
        assert is_retryable_error(TimeoutError("timed out")) is True

    def test_description_includes_status_and_kind(self):
        text = describe_provider_error("grok/grok-4.5", _err(_BALANCE_402))
        assert "grok/grok-4.5" in text
        assert "status=402" in text
        assert "unrecoverable" in text


class TestCallFirstAvailableProvider:
    def test_falls_through_503_to_next_provider(self):
        primary = _Provider(error=_err(_UPSTREAM_503))
        backup = _Provider(text='{"summary": "ok"}')
        ctx = _Context({"grok/grok-4.5": primary, "伞云智联/grok-4.5": backup})

        resp = asyncio.run(
            call_first_available_provider(
                ctx,
                ["grok/grok-4.5", "伞云智智联/typo-ignored", "伞云智联/grok-4.5"],
                prompt="p",
                system_prompt="s",
            )
        )

        assert resp.completion_text == '{"summary": "ok"}'
        assert len(primary.calls) == 1
        assert len(backup.calls) == 1

    def test_system_prompt_is_passed_through_not_concatenated(self):
        """整合与标签提取依赖 system_prompt 约束 JSON 输出，不能被拼进 prompt。"""
        provider = _Provider(text="[]")
        ctx = _Context({"p1": provider})

        asyncio.run(
            call_first_available_provider(
                ctx, ["p1"], prompt="body", system_prompt="only JSON"
            )
        )

        assert provider.calls[0]["prompt"] == "body"
        assert provider.calls[0]["system_prompt"] == "only JSON"

    def test_402_then_503_then_success(self):
        first = _Provider(error=_err(_BALANCE_402))
        second = _Provider(error=_err(_UPSTREAM_503))
        third = _Provider(text="done")
        ctx = _Context({"a": first, "b": second, "c": third})

        resp = asyncio.run(
            call_first_available_provider(ctx, ["a", "b", "c"], prompt="p")
        )

        assert resp.completion_text == "done"
        assert ctx.lookups == ["a", "b", "c"]

    def test_empty_completion_advances_to_next_provider(self):
        empty = _Provider(text="")
        good = _Provider(text="real")
        ctx = _Context({"a": empty, "b": good})

        resp = asyncio.run(call_first_available_provider(ctx, ["a", "b"], prompt="p"))

        assert resp.completion_text == "real"

    def test_raises_last_error_when_all_fail(self):
        ctx = _Context({"a": _Provider(error=_err(_UPSTREAM_503)),
                        "b": _Provider(error=_err(_BALANCE_402))})

        with pytest.raises(RuntimeError) as caught:
            asyncio.run(call_first_available_provider(ctx, ["a", "b"], prompt="p"))

        assert "402" in str(caught.value)

    def test_raises_when_chain_empty(self):
        with pytest.raises(RuntimeError, match="No provider_ids configured"):
            asyncio.run(call_first_available_provider(_Context({}), [], prompt="p"))

    def test_raises_when_no_provider_resolves(self):
        with pytest.raises(RuntimeError, match="No usable provider in chain"):
            asyncio.run(
                call_first_available_provider(_Context({}), ["missing"], prompt="p")
            )
