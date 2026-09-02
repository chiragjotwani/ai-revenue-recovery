"""Diagnose a recovery case (Phase 4).

Orchestrates: build the bounded context -> run the configured reasoning
model -> validate the output -> persist the diagnosis (Section 51) ->
advance the case ``detected -> diagnosing -> diagnosed``.

The model only diagnoses. It does not decide and it does not act -- there
is no action executor in the codebase yet, and the diagnosis path has no
write access to payments (ADR-003). If the model output cannot be
validated, the case is left in ``diagnosing`` and nothing is persisted;
the caller surfaces that as an upstream (502) failure and a retry can
re-run from ``diagnosing``.

Phase 10 addition: the configured provider is run through
``app.ai.providers.router.run_diagnosis_with_failover`` rather than bare
``run_diagnosis``. This changes nothing about the frozen Phase 4 contract
above -- a validation failure still leaves the case in ``diagnosing`` and
persists nothing, still surfaced as 502 -- it only adds an escalation to
``MockProvider`` when the *configured* provider is transport-unreachable
(``ReasoningModelError``), so a real model outage does not by itself stop
every case from being diagnosable. See
``app.ai.providers.router``'s module docstring for the (deliberately
failure-only, never confidence-based) escalation rule.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_builder import build_recovery_context
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import ReasoningModel
from app.ai.providers.factory import get_reasoning_model
from app.ai.providers.router import run_diagnosis_with_failover
from app.core.errors import CaseNotDiagnosableError
from app.models.diagnosis import Diagnosis as DiagnosisRow
from app.models.recovery import RecoveryCase, RecoveryCaseState
from app.recovery import service as recovery_service

_DIAGNOSABLE_FROM = {RecoveryCaseState.DETECTED, RecoveryCaseState.DIAGNOSING}
_ACTOR = "system:diagnose"


async def get_latest_diagnosis(session: AsyncSession, case_id: UUID) -> DiagnosisRow | None:
    row: DiagnosisRow | None = await session.scalar(
        select(DiagnosisRow)
        .where(DiagnosisRow.case_id == case_id)
        .order_by(DiagnosisRow.created_at.desc(), DiagnosisRow.id.desc())
        .limit(1)
    )
    return row


async def diagnose_case(
    session: AsyncSession,
    case_id: UUID,
    *,
    provider: ReasoningModel | None = None,
) -> tuple[RecoveryCase, DiagnosisRow]:
    provider = provider or get_reasoning_model()
    case = await recovery_service.get_case(session, case_id)  # raises if unknown

    if case.state not in _DIAGNOSABLE_FROM:
        raise CaseNotDiagnosableError(case.state.value)

    if case.state is RecoveryCaseState.DETECTED:
        case = await recovery_service.transition_case(
            session,
            case_id,
            RecoveryCaseState.DIAGNOSING,
            actor=_ACTOR,
            reason="diagnosis started",
        )

    context = await build_recovery_context(session, case)
    # May raise ReasoningModelError (transport, from the fallback provider
    # too -- see run_diagnosis_with_failover) or DiagnosisValidationError
    # (unusable output). Either way the case stays in DIAGNOSING and no row
    # is written -- the caller turns it into a 502.
    result = await run_diagnosis_with_failover(
        context, provider, prompt_version=DIAGNOSIS_PROMPT_VERSION
    )
    diagnosis, raw = result.diagnosis, result.raw

    row = DiagnosisRow(
        case_id=case.id,
        outcome=diagnosis.outcome.value,
        disposition=diagnosis.disposition.value,
        confidence=Decimal(str(diagnosis.confidence)),
        reasoning=diagnosis.reasoning,
        recommended_strategy=diagnosis.recommended_strategy.value,
        recommended_delay_hours=diagnosis.recommended_delay_hours,
        schema_version=diagnosis.schema_version,
        model_name=raw.model_name,
        model_version=raw.model_version,
        prompt_version=raw.prompt_version,
        latency_ms=raw.latency_ms,
        router_escalated=result.escalated,
        router_escalation_reason=result.escalation_reason,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    case = await recovery_service.transition_case(
        session,
        case_id,
        RecoveryCaseState.DIAGNOSED,
        actor=_ACTOR,
        reason=f"diagnosed: {diagnosis.outcome.value} (confidence {diagnosis.confidence})",
    )
    return case, row
