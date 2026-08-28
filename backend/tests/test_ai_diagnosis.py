"""Diagnosis parsing, schema validation, and safeguard tests (Section 37).

Pure: constructs RecoveryContext objects directly, no database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ai.context_builder import (
    CustomerSummary,
    FailureSummary,
    PaymentSummary,
    RecoveryContext,
)
from app.ai.diagnosis import DiagnosisValidationError, _extract_json_object, run_diagnosis
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError
from app.ai.providers.mock import MockProvider
from app.ai.schema import (
    DiagnosisDisposition,
    DiagnosisOutcome,
    ModelDiagnosisJSON,
    RecoveryStrategy,
    disposition_for,
)

_PROMPT_V = "diagnosis_prompt_v1"


def _context(
    *,
    failure_reason: str | None = "insufficient_funds",
    evidence: str = "sufficient",
    conflict: bool = False,
    consecutive_failures: int = 1,
    success_rate: float = 0.75,
) -> RecoveryContext:
    return RecoveryContext(
        case_id=uuid.uuid4(),
        case_state="diagnosing",
        customer=CustomerSummary(
            external_id="cust-x",
            tenure_days=90,
            total_payments=4,
            successful_payments=3,
            historical_success_rate=success_rate,
        ),
        payment=PaymentSummary(
            external_reference="pay-x",
            amount=Decimal("4999.00"),
            currency="INR",
            status="failed",
            failure_reason=failure_reason,
            occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        failure=FailureSummary(
            consecutive_failures=consecutive_failures,
            distinct_prior_failure_reasons=[],
            days_since_last_success=30,
        ),
        recent_history=[],
        previous_interventions=[],
        applicable_policies=["A payment may be retried at most 3 times."],
        evidence_sufficiency=evidence,  # type: ignore[arg-type]
        signals_conflict=conflict,
    )


class _StubProvider(ReasoningModel):
    def __init__(self, text: str) -> None:
        self.name = "stub"
        self._text = text

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        return RawModelResponse(
            text=self._text,
            model_name="stub",
            model_version="0",
            prompt_version=prompt_version,
            latency_ms=1,
        )


class _ExplodingProvider(ReasoningModel):
    name = "boom"

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        raise ReasoningModelError("connection refused")


# --- JSON extraction -------------------------------------------------------


def test_extract_plain_object() -> None:
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_from_code_fence() -> None:
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_with_surrounding_prose() -> None:
    assert _extract_json_object('Here is the answer: {"a": 1}. Done.') == {"a": 1}


def test_extract_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        _extract_json_object("I cannot help with that.")


def test_extract_rejects_json_array() -> None:
    with pytest.raises(ValueError):
        _extract_json_object("[1, 2, 3]")


# --- schema validation ---------------------------------------------------


def test_disposition_mapping_is_total() -> None:
    for outcome in DiagnosisOutcome:
        assert isinstance(disposition_for(outcome), DiagnosisDisposition)


def test_valid_model_json_parses() -> None:
    obj = ModelDiagnosisJSON.model_validate(
        {
            "outcome": "insufficient_funds",
            "confidence": 0.9,
            "reasoning": "clear",
            "recommended_strategy": "retry",
            "recommended_delay_hours": 6,
        }
    )
    assert obj.outcome is DiagnosisOutcome.INSUFFICIENT_FUNDS


@pytest.mark.parametrize(
    "patch",
    [
        {"outcome": "not_a_real_outcome"},
        {"confidence": 1.5},
        {"confidence": "high"},
        {"reasoning": "   "},
        {"recommended_strategy": "delete_customer"},
        {"recommended_delay_hours": -3},
        {"extra_field": "nope"},
    ],
)
def test_invalid_model_json_is_rejected(patch: dict[str, object]) -> None:
    base = {
        "outcome": "insufficient_funds",
        "confidence": 0.9,
        "reasoning": "clear",
        "recommended_strategy": "retry",
        "recommended_delay_hours": 6,
    }
    base.update(patch)
    with pytest.raises(ValidationError):
        ModelDiagnosisJSON.model_validate(base)


# --- run_diagnosis with the mock provider ------------------------------


async def test_mock_diagnoses_insufficient_funds_confidently() -> None:
    diagnosis, raw = await run_diagnosis(_context(), MockProvider(), prompt_version=_PROMPT_V)
    assert diagnosis.outcome is DiagnosisOutcome.INSUFFICIENT_FUNDS
    assert diagnosis.disposition is DiagnosisDisposition.RETRIABLE_TRANSIENT
    assert diagnosis.recommended_strategy is RecoveryStrategy.RETRY
    assert diagnosis.recommended_delay_hours == 6
    assert diagnosis.confidence >= 0.8
    assert raw.model_name == "mock"
    assert raw.prompt_version == _PROMPT_V


async def test_incomplete_context_yields_unknown() -> None:
    # Section 37: hallucination case -- incomplete context must not be guessed.
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason=None, evidence="sparse"),
        MockProvider(),
        prompt_version=_PROMPT_V,
    )
    assert diagnosis.outcome is DiagnosisOutcome.UNKNOWN


async def test_conflicting_evidence_yields_low_confidence() -> None:
    # Section 37: conflicting evidence -> low confidence.
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason="fraud_suspected", conflict=True),
        MockProvider(),
        prompt_version=_PROMPT_V,
    )
    assert diagnosis.confidence <= 0.5


async def test_invalid_json_from_model_raises_and_never_executes() -> None:
    # Section 37: invalid JSON -> validation failure, never proceed.
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(
            _context(), _StubProvider("I'm not going to answer in JSON."), prompt_version=_PROMPT_V
        )


async def test_schema_violating_json_raises() -> None:
    with pytest.raises(DiagnosisValidationError):
        await run_diagnosis(
            _context(),
            _StubProvider('{"outcome": "wat", "confidence": 2, "reasoning": ""}'),
            prompt_version=_PROMPT_V,
        )


async def test_transport_error_propagates() -> None:
    with pytest.raises(ReasoningModelError):
        await run_diagnosis(_context(), _ExplodingProvider(), prompt_version=_PROMPT_V)


# --- safeguards against context-ignoring output -----------------------


async def test_safeguard_downgrades_overconfident_answer_on_sparse_context() -> None:
    overconfident = _StubProvider(
        '{"outcome": "insufficient_funds", "confidence": 0.97, "reasoning": "sure",'
        ' "recommended_strategy": "retry", "recommended_delay_hours": 6}'
    )
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason=None, evidence="sparse"), overconfident, prompt_version=_PROMPT_V
    )
    assert diagnosis.outcome is DiagnosisOutcome.UNKNOWN
    assert diagnosis.confidence <= 0.3
    assert diagnosis.recommended_strategy is RecoveryStrategy.MANUAL_REVIEW


async def test_safeguard_caps_confidence_when_signals_conflict() -> None:
    overconfident = _StubProvider(
        '{"outcome": "fraud_suspected", "confidence": 0.95, "reasoning": "sure",'
        ' "recommended_strategy": "manual_review", "recommended_delay_hours": null}'
    )
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason="fraud_suspected", conflict=True),
        overconfident,
        prompt_version=_PROMPT_V,
    )
    assert diagnosis.confidence <= 0.5
    assert diagnosis.outcome is DiagnosisOutcome.FRAUD_SUSPECTED
