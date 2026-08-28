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
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_builder import build_recovery_context
from app.ai.diagnosis import run_diagnosis
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import ReasoningModel
from app.ai.providers.factory import get_reasoning_model
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
    # May raise ReasoningModelError (transport) or DiagnosisValidationError
    # (unusable output). Either way the case stays in DIAGNOSING and no row
    # is written -- the caller turns it into a 502.
    diagnosis, raw = await run_diagnosis(context, provider, prompt_version=DIAGNOSIS_PROMPT_VERSION)

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
