"""Provider tests: the deterministic mock, the HTTP client (with a faked
transport, no model server needed), and provider selection.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.ai.context_builder import (
    CustomerSummary,
    FailureSummary,
    PaymentSummary,
    RecoveryContext,
)
from app.ai.providers.base import ReasoningModelError
from app.ai.providers.factory import get_reasoning_model
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.ai.providers.qwen import QwenProvider
from app.ai.schema import DiagnosisOutcome, ModelDiagnosisJSON
from app.core.config import Settings

_PROMPT_V = "diagnosis_prompt_v1"


def _context(failure_reason: str | None = "card_expired") -> RecoveryContext:
    return RecoveryContext(
        case_id=uuid.uuid4(),
        case_state="diagnosing",
        customer=CustomerSummary(
            external_id="c",
            tenure_days=10,
            total_payments=3,
            successful_payments=2,
            historical_success_rate=0.67,
        ),
        payment=PaymentSummary(
            external_reference="p",
            amount=Decimal("100.00"),
            currency="INR",
            status="failed",
            failure_reason=failure_reason,
            occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        failure=FailureSummary(
            consecutive_failures=1, distinct_prior_failure_reasons=[], days_since_last_success=5
        ),
        recent_history=[],
        previous_interventions=[],
        applicable_policies=[],
        evidence_sufficiency="sufficient",
        signals_conflict=False,
    )


# --- mock provider ------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("insufficient_funds", DiagnosisOutcome.INSUFFICIENT_FUNDS),
        ("card_expired", DiagnosisOutcome.CARD_EXPIRED),
        ("fraud_suspected", DiagnosisOutcome.FRAUD_SUSPECTED),
        ("something_unmapped", DiagnosisOutcome.UNKNOWN),
    ],
)
async def test_mock_provider_emits_valid_json_for_each_reason(
    reason: str, expected: DiagnosisOutcome
) -> None:
    raw = await MockProvider().diagnose(_context(reason), prompt_version=_PROMPT_V)
    parsed = ModelDiagnosisJSON.model_validate(json.loads(raw.text))
    assert parsed.outcome is expected


# --- OpenAI-compatible HTTP client (faked transport) ------------------


def _chat_completion(content: str, model: str = "qwen3:4b") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
        },
    )


async def test_http_provider_parses_a_normal_completion() -> None:
    body = json.dumps(
        {
            "outcome": "card_expired",
            "confidence": 0.88,
            "reasoning": "card expired",
            "recommended_strategy": "request_payment_method_update",
            "recommended_delay_hours": None,
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:4b"
        assert payload["messages"][0]["role"] == "system"
        return _chat_completion(body)

    provider = OpenAICompatibleProvider(
        name="qwen",
        base_url="http://fake:11434/v1",
        model="qwen3:4b",
        transport=httpx.MockTransport(handler),
    )
    raw = await provider.diagnose(_context(), prompt_version=_PROMPT_V)
    assert raw.model_name == "qwen"
    assert raw.model_version == "qwen3:4b"
    parsed = ModelDiagnosisJSON.model_validate(json.loads(raw.text))
    assert parsed.outcome is DiagnosisOutcome.CARD_EXPIRED


async def test_http_provider_raises_on_server_error() -> None:
    provider = OpenAICompatibleProvider(
        name="qwen",
        base_url="http://fake:11434/v1",
        model="qwen3:4b",
        transport=httpx.MockTransport(lambda _req: httpx.Response(500, text="boom")),
    )
    with pytest.raises(ReasoningModelError):
        await provider.diagnose(_context(), prompt_version=_PROMPT_V)


async def test_http_provider_raises_on_unexpected_shape() -> None:
    provider = OpenAICompatibleProvider(
        name="qwen",
        base_url="http://fake:11434/v1",
        model="qwen3:4b",
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"nope": True})),
    )
    with pytest.raises(ReasoningModelError):
        await provider.diagnose(_context(), prompt_version=_PROMPT_V)


async def test_http_provider_raises_on_empty_completion() -> None:
    provider = OpenAICompatibleProvider(
        name="qwen",
        base_url="http://fake:11434/v1",
        model="qwen3:4b",
        transport=httpx.MockTransport(lambda _req: _chat_completion("   ")),
    )
    with pytest.raises(ReasoningModelError):
        await provider.diagnose(_context(), prompt_version=_PROMPT_V)


# --- provider selection ----------------------------------------------


def test_factory_defaults_to_mock() -> None:
    assert isinstance(get_reasoning_model(Settings(reasoning_provider="mock")), MockProvider)


def test_factory_falls_back_to_mock_when_qwen_url_missing() -> None:
    settings = Settings(reasoning_provider="qwen", ai_qwen_base_url=None)
    assert isinstance(get_reasoning_model(settings), MockProvider)


def test_factory_returns_qwen_when_configured() -> None:
    settings = Settings(reasoning_provider="qwen", ai_qwen_base_url="http://localhost:11434/v1")
    assert isinstance(get_reasoning_model(settings), QwenProvider)
