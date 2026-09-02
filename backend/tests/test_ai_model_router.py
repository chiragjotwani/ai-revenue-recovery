"""Phase 10: unit tests for the model router
(``app.ai.providers.factory.select_reasoning_model``,
``app.ai.providers.router.run_diagnosis_with_failover``) and integration
tests for its wiring into ``diagnose_case``.

Scope boundary asserted explicitly: escalation is driven only by an
observable transport failure, never by the diagnosis's self-reported
confidence -- see ``app/ai/providers/router.py``'s module docstring.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_builder import (
    CustomerSummary,
    FailureSummary,
    PaymentSummary,
    RecoveryContext,
)
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError
from app.ai.providers.factory import get_reasoning_model, select_reasoning_model
from app.ai.providers.mock import MockProvider
from app.ai.providers.router import run_diagnosis_with_failover
from app.core.config import Settings
from app.models.diagnosis import Diagnosis as DiagnosisRow

_PROMPT_V = DIAGNOSIS_PROMPT_VERSION


def _context() -> RecoveryContext:
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
            failure_reason="insufficient_funds",
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


class _UnreachableProvider(ReasoningModel):
    name = "unreachable"

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        raise ReasoningModelError("connection refused")


class _AlwaysMockNamed(ReasoningModel):
    """A provider that reports itself as `mock` but is not actually
    MockProvider -- used to test the "already the fallback, nothing to
    escalate to" branch without relying on identity.
    """

    name = "mock"

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        raise ReasoningModelError("even the fallback is down")


# --- select_reasoning_model: explicit, observable substitution -------------


def test_select_reasoning_model_agrees_with_get_reasoning_model_for_mock() -> None:
    settings = Settings(reasoning_provider="mock")
    selection = select_reasoning_model(settings)
    assert isinstance(selection.provider, MockProvider)
    assert selection.requested_provider == "mock"
    assert selection.resolved_provider == "mock"
    assert selection.substituted is False
    assert selection.substitution_reason is None
    assert type(selection.provider) is type(get_reasoning_model(settings))


def test_select_reasoning_model_reports_substitution_when_qwen_url_unset() -> None:
    settings = Settings(reasoning_provider="qwen", ai_qwen_base_url=None)
    selection = select_reasoning_model(settings)
    assert isinstance(selection.provider, MockProvider)
    assert selection.requested_provider == "qwen"
    assert selection.resolved_provider == "mock"
    assert selection.substituted is True
    assert selection.substitution_reason is not None
    assert "AI_QWEN_BASE_URL" in selection.substitution_reason


def test_select_reasoning_model_no_substitution_when_qwen_url_set() -> None:
    settings = Settings(reasoning_provider="qwen", ai_qwen_base_url="http://localhost:11434/v1")
    selection = select_reasoning_model(settings)
    assert selection.resolved_provider == "qwen"
    assert selection.substituted is False


def test_provider_selection_is_immutable() -> None:
    selection = select_reasoning_model(Settings(reasoning_provider="mock"))
    with pytest.raises(AttributeError):
        selection.substituted = False  # type: ignore[misc]


# --- run_diagnosis_with_failover: failure-only escalation -------------------


async def test_primary_success_never_escalates() -> None:
    result = await run_diagnosis_with_failover(_context(), MockProvider(), prompt_version=_PROMPT_V)
    assert result.escalated is False
    assert result.escalation_reason is None
    assert result.raw.model_name == "mock"


async def test_transport_failure_escalates_to_fallback() -> None:
    result = await run_diagnosis_with_failover(
        _context(), _UnreachableProvider(), prompt_version=_PROMPT_V
    )
    assert result.escalated is True
    assert result.escalation_reason is not None
    assert "unreachable" in result.escalation_reason
    assert result.raw.model_name == "mock"  # the default fallback actually served it
    assert result.diagnosis is not None


async def test_primary_already_the_fallback_reraises_without_a_second_attempt() -> None:
    with pytest.raises(ReasoningModelError):
        await run_diagnosis_with_failover(
            _context(), _AlwaysMockNamed(), prompt_version=_PROMPT_V, fallback=MockProvider()
        )


async def test_validation_failure_never_escalates() -> None:
    """A parse/validation failure is run_diagnosis's own concern (retried
    against the SAME provider) -- the router must never treat it as a
    transport failure and must never silently substitute a different
    provider's answer for a genuinely bad one.
    """
    from app.ai.diagnosis import DiagnosisValidationError

    class _BadTextProvider(ReasoningModel):
        name = "bad-text"

        async def diagnose(
            self, context: RecoveryContext, *, prompt_version: str
        ) -> RawModelResponse:
            return RawModelResponse(
                text="not json at all",
                model_name="bad-text",
                model_version="0",
                prompt_version=prompt_version,
                latency_ms=1,
            )

    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis_with_failover(_context(), _BadTextProvider(), prompt_version=_PROMPT_V)


# --- confidence never drives escalation (explicit scope-boundary test) -----


def test_router_module_has_no_confidence_based_escalation_path() -> None:
    """Structural guarantee, not just a docstring claim: the router's
    public API takes no confidence/threshold parameter at all.
    """
    import inspect

    params = list(inspect.signature(run_diagnosis_with_failover).parameters)
    assert params == ["context", "primary", "prompt_version", "fallback"]
    for forbidden in ("confidence", "threshold", "probability"):
        assert forbidden not in params


# --- integration: diagnose_case escalates on a real transport failure ------


async def _ingest_and_open(client: AsyncClient, *, external_reference: str) -> uuid.UUID:
    payload = {
        "idempotency_key": external_reference,
        "event_type": "payment.failed",
        "source": "test-suite",
        "occurred_at": datetime(2026, 3, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": f"cust-{external_reference}", "email": "router@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201
    payment_id = r.json()["payment_id"]
    case_r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert case_r.status_code == 201
    return uuid.UUID(case_r.json()["id"])


async def test_diagnose_case_escalates_to_mock_when_configured_provider_is_unreachable(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.diagnosis.get_reasoning_model", lambda: _UnreachableProvider()
    )
    case_id = await _ingest_and_open(client, external_reference="router1")

    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text  # succeeded via escalation, not a 502
    assert r.json()["model_name"] == "mock"

    row = await db_session.scalar(select(DiagnosisRow).where(DiagnosisRow.case_id == case_id))
    assert row is not None
    assert row.model_name == "mock"


async def test_diagnose_case_still_502s_when_validation_fails_no_false_escalation(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: a validation failure (unparseable text) must
    still 502, exactly as the frozen Phase 4 contract requires -- the
    router must not accidentally paper over it with a fallback provider's
    different (and unrelated) answer.
    """

    class _BadProvider(ReasoningModel):
        name = "bad"

        async def diagnose(
            self, context: RecoveryContext, *, prompt_version: str
        ) -> RawModelResponse:
            return RawModelResponse(
                text="I refuse to answer in JSON.",
                model_name="bad",
                model_version="0",
                prompt_version=prompt_version,
                latency_ms=1,
            )

    monkeypatch.setattr("app.services.diagnosis.get_reasoning_model", lambda: _BadProvider())
    case_id = await _ingest_and_open(client, external_reference="router2")

    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 502
