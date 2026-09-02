"""Revenue measurement service (Phase 8): orchestration, persistence,
idempotency, and the aggregate report.

Two responsibilities, deliberately separate:

* :func:`measure_case` -- the idempotent, persisted, per-case audit
  artifact ("this case's current outcome was formally measured at time
  X"). Mirrors ``app.decision.service.decide_case`` /
  ``app.decision.actions.schedule_action`` /
  ``app.outcome.service.observe_outcome``'s own idempotency shape and
  KI-008 discipline exactly.
* :func:`get_revenue_report` -- a LIVE aggregate computed directly from
  ``RecoveryCase`` + ``DecisionResult`` + ``Payment`` + the current
  ``RecoveryOutcomeObservation`` per case, the same "compute live from
  the source tables" pattern ``app.risk.service.get_risk_summary`` already
  uses. It does NOT read from ``RevenueMeasurement`` -- that would make
  the dashboard depend on every case having had an explicit ``/measure``
  call first, a staleness trap this module avoids by never introducing.

Scope boundary (mirrors every prior phase's own docstring): this module
never calls an AI provider, never re-runs the Phase 5 policy engine,
never executes a Phase 6 action, never re-classifies a Phase 7 outcome,
and never accepts a monetary amount from a caller -- every amount is read
from ``app.models.payment.Payment``, the sole source of truth (Phase 8
security requirement).

Attribution: uses ONLY the Phase 7 ``RecoveryOutcomeObservation`` a case
already has. No fuzzy matching, no new correlation rule -- if a case has
no observation, it is not measurable yet (:class:`CaseNotMeasurableError`),
full stop.

Currency: KI-006 remains unresolved. Every aggregate in this module is a
per-currency list (``app.measurement.schema.CurrencyAmount`` /
``BreakdownEntry``); nothing here sums across currencies.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CaseNotMeasurableError
from app.decision.actions import get_action_for_case
from app.measurement.schema import BreakdownEntry, CurrencyAmount, RevenueReport
from app.models.decision import DecisionResult
from app.models.measurement import RevenueMeasurement
from app.models.payment import Payment
from app.models.recovery import RecoveryCase
from app.outcome.schema import ObservedOutcome
from app.outcome.service import get_outcome_for_case
from app.recovery import service as recovery_service
from app.services.diagnosis import get_latest_diagnosis


async def _get_existing_measurement(
    session: AsyncSession, case_id: UUID, outcome_observation_id: UUID
) -> RevenueMeasurement | None:
    result: RevenueMeasurement | None = await session.scalar(
        select(RevenueMeasurement)
        .where(RevenueMeasurement.case_id == case_id)
        .where(RevenueMeasurement.outcome_observation_id == outcome_observation_id)
    )
    return result


async def get_measurement_for_case(
    session: AsyncSession, case_id: UUID
) -> RevenueMeasurement | None:
    """The measurement tied to a case's CURRENT (latest) outcome
    observation, if one has been taken -- the read-only counterpart to
    :func:`measure_case`. Returns ``None`` for a case with no outcome yet,
    or one never explicitly measured, never raises for either.
    """
    outcome = await get_outcome_for_case(session, case_id)
    if outcome is None:
        return None
    return await _get_existing_measurement(session, case_id, outcome.id)


async def measure_case(session: AsyncSession, case_id: UUID) -> tuple[RevenueMeasurement, bool]:
    """Measure a case's current, observed outcome.

    Returns ``(measurement, created)``. Idempotent on
    ``(case_id, outcome_observation_id)``: a repeat call returns the
    existing row with ``created=False``. A case whose outcome later
    changes (a new, append-only Phase 7 observation attempt) is measured
    again on the next call, producing a legitimately new row tied to the
    new observation -- never a mutation of the old one, never a second
    count of the same fact.

    Never accepts, stores, or derives an amount/currency from anything
    but the case's own ``Payment`` row (via ``payment_id``) -- there is no
    parameter through which a caller could supply one.

    Raises :class:`~app.core.errors.RecoveryCaseNotFoundError` for an
    unknown case and :class:`CaseNotMeasurableError` if the case has no
    Phase 7 outcome observation yet.
    """
    case = await recovery_service.get_case(session, case_id)  # raises if unknown
    outcome = await get_outcome_for_case(session, case_id)
    if outcome is None:
        raise CaseNotMeasurableError(case_id)

    existing = await _get_existing_measurement(session, case.id, outcome.id)
    if existing is not None:
        return existing, False

    # Captured now, before any operation below can call rollback() -- same
    # MissingGreenlet hazard, and the same fix, as every prior phase's
    # service module (see app.decision.service.decide_case's docstring for
    # the original root-cause writeup).
    case_id_value = case.id
    payment_id = case.payment_id
    outcome_id = outcome.id
    outcome_status = outcome.outcome

    row = RevenueMeasurement(
        case_id=case_id_value,
        payment_id=payment_id,
        outcome_observation_id=outcome_id,
        status=outcome_status,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the (case_id,
        # outcome_observation_id) race. Re-check rather than assuming --
        # the database is what actually enforced uniqueness (KI-008), same
        # pattern as every prior phase's service module.
        existing = await _get_existing_measurement(session, case_id_value, outcome_id)
        if existing is not None:
            return existing, False
        raise

    await session.commit()
    await session.refresh(row)
    return row, True


# --------------------------------------------------------------------------
# Aggregate report (live computation -- see module docstring)
# --------------------------------------------------------------------------


async def _eligible_cases(session: AsyncSession) -> list[RecoveryCase]:
    """Cases that entered the decision pipeline (have a ``DecisionResult``)
    -- the denominator for "eligible at-risk value". Distinct from
    ``app.risk.service``'s ``revenue_at_risk`` (currently-still-at-risk,
    excludes anything ever resolved): this is every case the system
    determined was addressable, regardless of what happened next.
    """
    result = await session.scalars(
        select(RecoveryCase)
        .join(DecisionResult, DecisionResult.case_id == RecoveryCase.id)
        .distinct()
    )
    return list(result.all())


def _bucket_by_currency(rows: list[tuple[str, Decimal]]) -> list[CurrencyAmount]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for currency, amount in rows:
        totals[currency] += amount
        counts[currency] += 1
    return [
        CurrencyAmount(currency=currency, amount=totals[currency], case_count=counts[currency])
        for currency in sorted(totals)
    ]


def _bucket_by_key_and_currency(rows: list[tuple[str, str, Decimal]]) -> list[BreakdownEntry]:
    totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for key, currency, amount in rows:
        totals[(key, currency)] += amount
        counts[(key, currency)] += 1
    return [
        BreakdownEntry(
            key=key,
            currency=currency,
            amount=totals[(key, currency)],
            case_count=counts[(key, currency)],
        )
        for key, currency in sorted(totals)
    ]


async def get_revenue_report(session: AsyncSession) -> RevenueReport:
    """The full Phase 8 measurement report, computed live. See the module
    docstring for why this reads the source tables directly rather than
    ``RevenueMeasurement``.
    """
    cases = await _eligible_cases(session)

    at_risk_rows: list[tuple[str, Decimal]] = []
    recovered_rows: list[tuple[str, Decimal]] = []
    not_recovered_rows: list[tuple[str, Decimal]] = []
    unresolved_rows: list[tuple[str, Decimal]] = []
    strategy_rows: list[tuple[str, str, Decimal]] = []
    disposition_rows: list[tuple[str, str, Decimal]] = []
    recovered_case_count = 0

    for case in cases:
        payment = await session.get(Payment, case.payment_id)
        assert payment is not None  # guaranteed by RecoveryCase.payment_id's FK
        currency = payment.currency
        amount = payment.amount

        at_risk_rows.append((currency, amount))

        outcome = await get_outcome_for_case(session, case.id)
        status = outcome.outcome if outcome is not None else ObservedOutcome.UNRESOLVED.value

        if status == ObservedOutcome.RECOVERED.value:
            recovered_rows.append((currency, amount))
            recovered_case_count += 1

            action = await get_action_for_case(session, case.id)
            if action is not None:
                strategy_rows.append((action.action_type, currency, amount))

            diagnosis = await get_latest_diagnosis(session, case.id)
            if diagnosis is not None:
                disposition_rows.append((diagnosis.disposition, currency, amount))
        elif status == ObservedOutcome.NOT_RECOVERED.value:
            not_recovered_rows.append((currency, amount))
        else:
            unresolved_rows.append((currency, amount))

    eligible_count = len(cases)
    recovery_rate = (recovered_case_count / eligible_count) if eligible_count else 0.0

    return RevenueReport(
        eligible_case_count=eligible_count,
        eligible_at_risk=_bucket_by_currency(at_risk_rows),
        observed_recovered=_bucket_by_currency(recovered_rows),
        observed_not_recovered=_bucket_by_currency(not_recovered_rows),
        unresolved=_bucket_by_currency(unresolved_rows),
        recovered_case_count=recovered_case_count,
        observed_recovery_rate=recovery_rate,
        recovered_by_strategy=_bucket_by_key_and_currency(strategy_rows),
        recovered_by_disposition=_bucket_by_key_and_currency(disposition_rows),
    )
