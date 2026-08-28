"""Deterministic reasoning-model provider.

No network, no model weights. It derives a plausible diagnosis directly
from the context so that development, CI, and the whole test suite work
with zero infrastructure. It deliberately mirrors the behaviours the
mandatory AI test cases (Section 37) check:

* sparse context  -> ``UNKNOWN``
* conflicting signals -> low confidence
* a clear failure reason -> a confident, specific outcome

The response is emitted as a JSON string and goes through the exact same
parse/validate path as a real provider.
"""

from __future__ import annotations

import json
import time

from app.ai.context_builder import RecoveryContext
from app.ai.providers.base import RawModelResponse, ReasoningModel
from app.ai.schema import DiagnosisOutcome, RecoveryStrategy

_REASON_TO_OUTCOME: dict[str, DiagnosisOutcome] = {
    "insufficient_funds": DiagnosisOutcome.INSUFFICIENT_FUNDS,
    "card_expired": DiagnosisOutcome.CARD_EXPIRED,
    "do_not_honor": DiagnosisOutcome.DO_NOT_HONOR,
    "processing_error": DiagnosisOutcome.PROCESSING_ERROR,
    "stolen_card": DiagnosisOutcome.STOLEN_CARD,
    "lost_card": DiagnosisOutcome.LOST_CARD,
    "fraud_suspected": DiagnosisOutcome.FRAUD_SUSPECTED,
    "authentication_required": DiagnosisOutcome.AUTHENTICATION_REQUIRED,
    "card_not_supported": DiagnosisOutcome.CARD_NOT_SUPPORTED,
}

_STRATEGY_BY_OUTCOME: dict[DiagnosisOutcome, tuple[RecoveryStrategy, int | None]] = {
    DiagnosisOutcome.INSUFFICIENT_FUNDS: (RecoveryStrategy.RETRY, 6),
    DiagnosisOutcome.DO_NOT_HONOR: (RecoveryStrategy.RETRY, 12),
    DiagnosisOutcome.PROCESSING_ERROR: (RecoveryStrategy.RETRY, 1),
    DiagnosisOutcome.CARD_EXPIRED: (RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE, None),
    DiagnosisOutcome.CARD_NOT_SUPPORTED: (RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE, None),
    DiagnosisOutcome.AUTHENTICATION_REQUIRED: (RecoveryStrategy.CONTACT_CUSTOMER, None),
    DiagnosisOutcome.STOLEN_CARD: (RecoveryStrategy.MANUAL_REVIEW, None),
    DiagnosisOutcome.LOST_CARD: (RecoveryStrategy.MANUAL_REVIEW, None),
    DiagnosisOutcome.FRAUD_SUSPECTED: (RecoveryStrategy.MANUAL_REVIEW, None),
    DiagnosisOutcome.UNKNOWN: (RecoveryStrategy.MANUAL_REVIEW, None),
}


class MockProvider(ReasoningModel):
    name = "mock"

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        start = time.perf_counter()
        outcome, confidence, reasoning = self._decide(context)
        strategy, delay = _STRATEGY_BY_OUTCOME[outcome]
        body = {
            "outcome": outcome.value,
            "confidence": confidence,
            "reasoning": reasoning,
            "recommended_strategy": strategy.value,
            "recommended_delay_hours": delay,
        }
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RawModelResponse(
            text=json.dumps(body),
            model_name="mock",
            model_version="1",
            prompt_version=prompt_version,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _decide(context: RecoveryContext) -> tuple[DiagnosisOutcome, float, str]:
        if context.evidence_sufficiency == "sparse":
            return (
                DiagnosisOutcome.UNKNOWN,
                0.2,
                "Insufficient evidence: no failure reason or no payment history to judge a cause.",
            )

        reason = context.payment.failure_reason or ""
        outcome = _REASON_TO_OUTCOME.get(reason, DiagnosisOutcome.UNKNOWN)

        if context.signals_conflict:
            return (
                outcome,
                0.3,
                (
                    f"Reported reason {reason!r} conflicts with a long, near-perfect "
                    "payment history; low confidence, recommend manual review."
                ),
            )

        if outcome is DiagnosisOutcome.UNKNOWN:
            return (
                DiagnosisOutcome.UNKNOWN,
                0.4,
                f"Failure reason {reason!r} is not a recognised cause.",
            )

        return (
            outcome,
            0.9,
            (
                f"Reported reason {reason!r} with {context.failure.consecutive_failures} "
                f"consecutive failure(s) since last success points clearly to this cause."
            ),
        )
