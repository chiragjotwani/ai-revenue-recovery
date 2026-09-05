"""AI failure-mode coverage (Phase 4.1, Workstream B6).

Complements test_ai_diagnosis.py / test_ai_providers.py by closing the
gaps the audit identified: request timeout, a *missing* (not merely wrong)
required field, an explicit bound on retries, and a model that returns
unsafe / irrelevant action text.

Required behaviour throughout: no invalid diagnosis is persisted, the
error is surfaced as a provider/validation error (the API turns those into
502), and retries are bounded -- never infinite.
"""

from __future__ import annotations

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
from app.ai.diagnosis import DiagnosisValidationError, run_diagnosis
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

_PROMPT_V = DIAGNOSIS_PROMPT_VERSION


def _context() -> RecoveryContext:
    return RecoveryContext(
        case_id=uuid.uuid4(),
        case_state="diagnosing",
        customer=CustomerSummary(
            external_id="c",
            tenure_days=90,
            total_payments=4,
            successful_payments=3,
            historical_success_rate=0.75,
        ),
        payment=PaymentSummary(
            external_reference="p",
            amount=Decimal("4999.00"),
            currency="INR",
            status="failed",
            failure_reason="insufficient_funds",
            occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        failure=FailureSummary(
            consecutive_failures=1, distinct_prior_failure_reasons=[], days_since_last_success=30
        ),
        recent_history=[],
        previous_interventions=[],
        applicable_policies=[],
        evidence_sufficiency="sufficient",
        signals_conflict=False,
    )


class _CountingStub(ReasoningModel):
    """Returns a fixed (bad) body and counts how many times it was called."""

    def __init__(self, text: str) -> None:
        self.name = "counting-stub"
        self._text = text
        self.calls = 0

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        self.calls += 1
        return RawModelResponse(
            text=self._text,
            model_name=self.name,
            model_version="0",
            prompt_version=prompt_version,
            latency_ms=1,
        )


async def test_request_timeout_is_a_reasoning_model_error() -> None:
    def _timeout(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=_req)

    provider = OpenAICompatibleProvider(
        name="qwen",
        base_url="http://fake:11434/v1",
        model="m",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(_timeout),
    )
    with pytest.raises(ReasoningModelError):
        await provider.diagnose(_context(), prompt_version=_PROMPT_V)


async def test_missing_required_field_is_rejected_and_not_persisted() -> None:
    # "outcome" absent entirely (previous tests only patched values, never removed keys).
    stub = _CountingStub(
        '{"confidence": 0.9, "reasoning": "x", "recommended_strategy": "retry",'
        ' "recommended_delay_hours": 6}'
    )
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(_context(), stub, prompt_version=_PROMPT_V)


async def test_retries_are_bounded_never_infinite() -> None:
    stub = _CountingStub("this is not JSON at all")
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(_context(), stub, prompt_version=_PROMPT_V, max_attempts=2)
    assert stub.calls == 2, "run_diagnosis must call the provider exactly max_attempts times"


async def test_model_recommending_an_unsafe_action_is_rejected() -> None:
    # The model asks for an action that is not in the RecoveryStrategy enum
    # ("wire_funds"). It must fail schema validation -- the model cannot
    # invent an action the platform does not know about.
    stub = _CountingStub(
        '{"outcome": "insufficient_funds", "confidence": 0.99,'
        ' "reasoning": "pay me", "recommended_strategy": "wire_funds",'
        ' "recommended_delay_hours": 0}'
    )
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(_context(), stub, prompt_version=_PROMPT_V)


async def test_model_returning_prose_only_never_yields_a_diagnosis() -> None:
    stub = _CountingStub("Sure! I think the card just expired, you should ask them to update it.")
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(_context(), stub, prompt_version=_PROMPT_V)
