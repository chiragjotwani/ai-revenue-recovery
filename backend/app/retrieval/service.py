"""Historical case retrieval service (Phase 11, scoped -- see
``app.retrieval.schema`` module docstring).

Two responsibilities:

* :func:`ensure_case_features` -- idempotent, persisted vector computation
  for one case (KI-008 discipline: flush -> ``IntegrityError`` -> rollback
  -> recheck, never a bare SELECT-then-INSERT, mirroring every prior
  phase's service module).
* :func:`find_similar_cases` -- deterministic cosine-similarity ranking
  against every OTHER diagnosed case's persisted vector. Never executes an
  action, never re-runs the policy engine, never modifies a decision, and
  never feeds anything back into Phase 4's diagnosis pipeline -- purely a
  read path.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schema import DiagnosisDisposition, DiagnosisOutcome
from app.decision.service import get_decision_for_case
from app.models.case_feature_vector import CaseFeatureVector
from app.models.diagnosis import Diagnosis
from app.models.payment import Payment
from app.outcome.service import get_outcome_for_case
from app.recovery import service as recovery_service
from app.retrieval.embedding import FEATURE_VERSION, compute_case_features, cosine_similarity
from app.retrieval.schema import SimilarCase
from app.risk.features import compute_risk_features
from app.services.diagnosis import get_latest_diagnosis


class NoFeaturesAvailableError(Exception):
    """Raised when retrieval is requested for a case with no persisted
    diagnosis to derive features from yet.
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} has no diagnosis to derive features from.")


async def _get_existing_vector(
    session: AsyncSession, case_id: UUID, diagnosis_id: UUID
) -> CaseFeatureVector | None:
    result: CaseFeatureVector | None = await session.scalar(
        select(CaseFeatureVector)
        .where(CaseFeatureVector.case_id == case_id)
        .where(CaseFeatureVector.diagnosis_id == diagnosis_id)
    )
    return result


async def ensure_case_features(session: AsyncSession, case_id: UUID) -> CaseFeatureVector:
    """Idempotently compute and persist the feature vector for a case's
    current diagnosis. Returns the existing row if one already exists for
    ``(case_id, diagnosis_id)`` -- a repeat call never creates a duplicate.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case and :class:`NoFeaturesAvailableError` if the case has no
    diagnosis yet.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    diagnosis = await get_latest_diagnosis(session, case.id)
    if diagnosis is None:
        raise NoFeaturesAvailableError(case_id)

    existing = await _get_existing_vector(session, case.id, diagnosis.id)
    if existing is not None:
        return existing

    payment = await session.get(Payment, case.payment_id)
    assert payment is not None  # guaranteed by RecoveryCase.payment_id's FK

    # Reuses the exact same Postgres-only risk features Phase 2's risk
    # scoring already computes for this payment -- not re-derived or
    # guessed, the identical function (app.risk.features.compute_risk_features).
    risk_features = await compute_risk_features(session, payment)

    # Captured now, before any operation below can call rollback() -- same
    # MissingGreenlet hazard, and the same fix, as every prior phase's
    # service module.
    case_id_value = case.id
    diagnosis_id = diagnosis.id
    features = compute_case_features(
        outcome=DiagnosisOutcome(diagnosis.outcome),
        disposition=DiagnosisDisposition(diagnosis.disposition),
        confidence=diagnosis.confidence,
        consecutive_failures=risk_features.consecutive_failures,
        historical_success_rate=risk_features.historical_success_rate,
        amount=payment.amount,
    )

    row = CaseFeatureVector(
        case_id=case_id_value,
        diagnosis_id=diagnosis_id,
        features=features,
        feature_version=FEATURE_VERSION,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await _get_existing_vector(session, case_id_value, diagnosis_id)
        if existing is not None:
            return existing
        raise

    await session.commit()
    await session.refresh(row)
    return row


async def find_similar_cases(
    session: AsyncSession, case_id: UUID, *, limit: int = 3
) -> list[SimilarCase]:
    """The top ``limit`` other diagnosed cases, ranked by deterministic
    feature-vector cosine similarity to ``case_id``'s current diagnosis.
    Never includes the case itself. Read-only: never schedules or
    executes an action, never changes a decision.
    """
    query_vector = await ensure_case_features(session, case_id)

    # Lazily backfill any other diagnosed case's vector the first time it
    # is needed as a candidate -- ensure_case_features is idempotent
    # (KI-008), so a case whose vector already exists is a cheap no-op
    # read. Without this, a case's vector would only ever exist after its
    # OWN retrieval endpoint had been called, leaving every other
    # diagnosed case invisible as a candidate.
    other_case_ids = (
        await session.scalars(
            select(Diagnosis.case_id).where(Diagnosis.case_id != case_id).distinct()
        )
    ).all()
    for other_case_id in other_case_ids:
        await ensure_case_features(session, other_case_id)

    candidates = list(
        (
            await session.scalars(
                select(CaseFeatureVector).where(CaseFeatureVector.case_id != case_id)
            )
        ).all()
    )

    scored = sorted(
        ((cosine_similarity(query_vector.features, c.features), c) for c in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )

    results: list[SimilarCase] = []
    for similarity, candidate in scored[:limit]:
        diagnosis = await session.get(Diagnosis, candidate.diagnosis_id)
        if diagnosis is None:
            continue
        decision = await get_decision_for_case(session, candidate.case_id)
        outcome = await get_outcome_for_case(session, candidate.case_id)
        results.append(
            SimilarCase(
                case_id=candidate.case_id,
                diagnosis_id=candidate.diagnosis_id,
                similarity=round(similarity, 4),
                disposition=diagnosis.disposition,
                outcome=diagnosis.outcome,
                approved_strategy=decision.approved_strategy if decision else None,
                decision_status=decision.decision_status if decision else None,
                observed_outcome=outcome.outcome if outcome else None,
            )
        )
    return results
