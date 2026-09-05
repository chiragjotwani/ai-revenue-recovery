"""Real reasoning-model integration test (Phase 4.1, Workstream B3).

SKIPPED unless ``AI_QWEN_BASE_URL`` points at a live OpenAI-compatible
endpoint (e.g. a local Ollama: ``AI_QWEN_BASE_URL=http://localhost:11434/v1``
``AI_QWEN_MODEL=qwen3:4b-instruct-2507-q8_0``). CI does not run it -- CI has
no model -- but it exists so the real path is exercised, not only mocked.

It asserts the *integration contract* (transport succeeds, the reply
validates against ``ModelDiagnosisJSON`` through ``run_diagnosis``, the
safeguards still fire). It deliberately does NOT assert diagnostic
accuracy -- that needs an independent ground-truth set and a real
evaluation, which does not exist yet (KI-007).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.ai.context_builder import (
    CustomerSummary,
    FailureSummary,
    PaymentSummary,
    RecoveryContext,
)
from app.ai.diagnosis import run_diagnosis
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.qwen import QwenProvider
from app.ai.schema import DiagnosisOutcome

_BASE_URL = os.environ.get("AI_QWEN_BASE_URL")
_MODEL = os.environ.get("AI_QWEN_MODEL", "qwen3:4b")

pytestmark = pytest.mark.skipif(
    not _BASE_URL,
    reason="AI_QWEN_BASE_URL not set -- real-model integration test skipped",
)


def _context(
    *,
    failure_reason: str | None,
    evidence: str = "sufficient",
    conflict: bool = False,
    successful: int = 3,
    rate: float = 0.75,
) -> RecoveryContext:
    return RecoveryContext(
        case_id=uuid.uuid4(),
        case_state="diagnosing",
        customer=CustomerSummary(
            external_id="cust-real",
            tenure_days=180,
            total_payments=successful + 1,
            successful_payments=successful,
            historical_success_rate=rate,
        ),
        payment=PaymentSummary(
            external_reference="pay-real",
            amount=Decimal("4999.00"),
            currency="INR",
            status="failed",
            failure_reason=failure_reason,
            occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        ),
        failure=FailureSummary(
            consecutive_failures=1, distinct_prior_failure_reasons=[], days_since_last_success=30
        ),
        recent_history=[],
        previous_interventions=[],
        applicable_policies=["A payment may be retried at most 3 times."],
        evidence_sufficiency=evidence,  # type: ignore[arg-type]
        signals_conflict=conflict,
    )


def _provider() -> QwenProvider:
    assert _BASE_URL is not None
    return QwenProvider(base_url=_BASE_URL, model=_MODEL, timeout_seconds=120.0)


@pytest.mark.parametrize(
    "reason",
    [
        "insufficient_funds",
        "authentication_required",
        "processing_error",
        "card_expired",
        "fraud_suspected",
        "quantum_flux_decline",  # unknown reason string
    ],
)
async def test_real_model_reply_validates_through_the_pipeline(reason: str) -> None:
    diagnosis, raw = await run_diagnosis(
        _context(failure_reason=reason), _provider(), prompt_version=DIAGNOSIS_PROMPT_VERSION
    )
    assert raw.model_name == "qwen"
    assert raw.model_version  # the server echoes a concrete model id
    assert 0.0 <= diagnosis.confidence <= 1.0
    assert diagnosis.disposition.value  # derived, always present


async def test_real_model_sparse_context_downgrades_to_unknown() -> None:
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason=None, evidence="sparse", successful=0, rate=0.0),
        _provider(),
        prompt_version=DIAGNOSIS_PROMPT_VERSION,
    )
    assert diagnosis.outcome is DiagnosisOutcome.UNKNOWN


async def test_real_model_conflicting_signals_confidence_is_capped() -> None:
    diagnosis, _ = await run_diagnosis(
        _context(failure_reason="stolen_card", conflict=True, successful=6, rate=0.95),
        _provider(),
        prompt_version=DIAGNOSIS_PROMPT_VERSION,
    )
    assert diagnosis.confidence <= 0.5
