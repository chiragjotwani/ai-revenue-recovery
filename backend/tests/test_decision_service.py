"""Phase 5C: decision service integration + concurrency tests.

Real Postgres (project policy: no mocking the database). Reuses the
existing ingest/open-case/diagnose flow (same helpers as
test_diagnosis_api.py) to reach a genuinely DIAGNOSED case, then exercises
app.decision.service.decide_case directly (no API endpoint exists yet --
that is 5F).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CaseNotDecidableError, RecoveryCaseNotFoundError
from app.db.session import AsyncSessionLocal
from app.decision.schema import DECISION_ENGINE_VERSION, DecisionStatus, Recoverability
from app.decision.service import _get_existing_decision, decide_case
from app.models.decision import DecisionResult as DecisionResultRow
from app.models.payment import Payment
from app.models.recovery import RecoveryCase, RecoveryCaseState, RecoveryCaseTransition
from app.services.diagnosis import get_latest_diagnosis

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-dec",
    occurred_at: datetime = BASE,
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "dec@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201
    return r.json()


async def _ingest(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, **kw: object
) -> dict:
    """Seed 3 successes then a failure for one customer -- sufficient
    evidence, no conflicting signals, matching test_diagnosis_api.py's
    canonical setup.
    """
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
        )
    return await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        failure_reason=str(kw.get("failure_reason", "insufficient_funds")),
        customer_external_id=customer_external_id,
    )


async def _open_case(client: AsyncClient, payment_id: str) -> uuid.UUID:
    r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def _diagnosed_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, failure_reason: str
) -> uuid.UUID:
    payment = await _ingest(
        client,
        external_reference=external_reference,
        customer_external_id=customer_external_id,
        failure_reason=failure_reason,
    )
    case_id = await _open_case(client, payment["payment_id"])
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text
    return case_id


# --- Happy path -----------------------------------------------------------


async def test_decide_persists_a_decision_and_transitions_the_case(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d1",
        customer_external_id="cust-d1",
        failure_reason="insufficient_funds",
    )

    async with AsyncSessionLocal() as session:
        case, decision = await decide_case(session, case_id)

    assert case.state is RecoveryCaseState.DECISION_PENDING
    assert decision.approved_strategy == "retry"
    assert decision.candidate_strategy == "retry"
    assert decision.decision_status == DecisionStatus.APPROVED.value
    assert decision.recoverability == Recoverability.LIKELY_RECOVERABLE.value
    assert decision.decision_engine_version == DECISION_ENGINE_VERSION
    assert decision.scheduled_not_before is not None

    row_count = await db_session.scalar(select(func.count()).select_from(DecisionResultRow))
    assert row_count == 1


async def test_decide_unknown_case_raises_not_found() -> None:
    async with AsyncSessionLocal() as session:
        with pytest.raises(RecoveryCaseNotFoundError):
            await decide_case(session, uuid.uuid4())


async def test_decide_before_diagnosis_is_not_decidable(client: AsyncClient) -> None:
    payment = await _ingest_one(
        client,
        idempotency_key="d2-f",
        external_reference="d2-f",
        customer_external_id="cust-d2",
    )
    case_id = await _open_case(client, payment["payment_id"])  # state: detected

    async with AsyncSessionLocal() as session:
        with pytest.raises(CaseNotDecidableError):
            await decide_case(session, case_id)


# --- Policy outcomes -------------------------------------------------------


async def test_decide_fraud_disposition_escalates(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d3",
        customer_external_id="cust-d3",
        failure_reason="fraud_suspected",
    )

    async with AsyncSessionLocal() as session:
        case, decision = await decide_case(session, case_id)

    assert decision.approved_strategy == "manual_review"
    assert decision.decision_status == DecisionStatus.ESCALATED.value
    assert decision.recoverability == Recoverability.NOT_RECOVERABLE_AUTOMATICALLY.value
    assert case.state is RecoveryCaseState.DECISION_PENDING


async def test_decide_sparse_evidence_escalates(client: AsyncClient) -> None:
    # A single failed payment with no failure_reason -> sparse evidence in
    # the context builder, regardless of what the mock diagnosis proposes.
    payment = await _ingest_one(
        client,
        idempotency_key="d4-f",
        external_reference="d4-f",
        failure_reason=None,
        customer_external_id="cust-d4",
    )
    case_id = await _open_case(client, payment["payment_id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200

    async with AsyncSessionLocal() as session:
        case, decision = await decide_case(session, case_id)

    assert decision.approved_strategy == "manual_review"
    assert decision.decision_status == DecisionStatus.ESCALATED.value
    assert decision.recoverability == Recoverability.INSUFFICIENT_EVIDENCE.value


async def test_decide_already_paid_routes_to_no_action(client: AsyncClient) -> None:
    payment = await _ingest(
        client,
        external_reference="d5",
        customer_external_id="cust-d5",
        failure_reason="insufficient_funds",
    )
    case_id = await _open_case(client, payment["payment_id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200

    # Customer pays by another attempt, strictly after the failed payment.
    await _ingest_one(
        client,
        idempotency_key="d5-late-success",
        external_reference="d5-late-success",
        event_type="payment.succeeded",
        failure_reason=None,
        customer_external_id="cust-d5",
        occurred_at=BASE + timedelta(hours=1),
    )

    async with AsyncSessionLocal() as session:
        case, decision = await decide_case(session, case_id)

    assert decision.approved_strategy == "no_action"
    assert decision.decision_status == DecisionStatus.APPROVED.value


async def test_rejected_and_superseded_are_not_currently_reachable() -> None:
    # Documents, rather than fabricates coverage for, a real property of
    # Phase 5B: every branch in app.decision.policy.evaluate resolves to
    # APPROVED or ESCALATED -- there is always a safe fallback -- so
    # REJECTED and SUPERSEDED exist in the Phase 5A vocabulary (for a
    # future workstream, e.g. a case decided against a diagnosis that is
    # no longer current) but are not producible today. Not testing a path
    # that cannot occur is more honest than asserting something false.
    assert DecisionStatus.REJECTED.value == "rejected"
    assert DecisionStatus.SUPERSEDED.value == "superseded"


# --- Sequential idempotency -------------------------------------------------


async def test_sequential_duplicate_decide_returns_the_same_decision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d6",
        customer_external_id="cust-d6",
        failure_reason="insufficient_funds",
    )

    async with AsyncSessionLocal() as s1:
        case1, decision1 = await decide_case(s1, case_id)
    async with AsyncSessionLocal() as s2:
        case2, decision2 = await decide_case(s2, case_id)

    assert decision1.id == decision2.id
    assert case1.state is case2.state is RecoveryCaseState.DECISION_PENDING

    row_count = await db_session.scalar(select(func.count()).select_from(DecisionResultRow))
    assert row_count == 1
    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.DECISION_PENDING)
    )
    assert transition_count == 1


async def test_deciding_an_already_decided_case_again_is_idempotent_not_an_error(
    client: AsyncClient,
) -> None:
    # A case that has moved to DECISION_PENDING is no longer "decidable"
    # in the sense of producing a *new* decision, but re-asking for it must
    # not raise -- it must return the existing one (see decide_case's own
    # idempotency-check-before-state-gate ordering).
    case_id = await _diagnosed_case(
        client,
        external_reference="d7",
        customer_external_id="cust-d7",
        failure_reason="insufficient_funds",
    )
    async with AsyncSessionLocal() as s1:
        _, first = await decide_case(s1, case_id)
    async with AsyncSessionLocal() as s2:
        _, second = await decide_case(s2, case_id)
    assert first.id == second.id


# --- ORM lifecycle regression: expired-attribute access after rollback -----
#
# Root cause (Phase 5C ORM lifecycle investigation): after
# ``await session.rollback()``, SQLAlchemy expires every attribute of
# every ORM object attached to the session, regardless of
# ``expire_on_commit``. ``decide_case`` used to evaluate ``diagnosis.id``
# as a plain (unawaited) Python expression immediately after such a
# rollback, in its IntegrityError-recovery branch. That is an
# expired-attribute access -- implicit lazy-load IO attempted from
# synchronous attribute-access code, which raises
# ``sqlalchemy.exc.MissingGreenlet`` under asyncio SQLAlchemy. The fix
# captures ``diagnosis_id`` before any rollback can occur and uses that
# local, immutable value everywhere afterward. This test proves the
# fixed pattern directly, deterministically, with zero concurrency
# needed -- the defect never depended on concurrency, only on reaching a
# rollback.


async def test_captured_diagnosis_id_survives_rollback_without_missinggreenlet(
    client: AsyncClient,
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d8-orm",
        customer_external_id="cust-d8-orm",
        failure_reason="insufficient_funds",
    )

    async with AsyncSessionLocal() as session:
        # 1. load the Diagnosis ORM object
        diagnosis = await get_latest_diagnosis(session, case_id)
        assert diagnosis is not None

        # 2. capture diagnosis_id -- the fix under test
        diagnosis_id = diagnosis.id

        # 3. trigger a genuine IntegrityError on the same unique
        # constraint decide_case's loser branch hits
        conflict = DecisionResultRow(
            case_id=case_id,
            diagnosis_id=diagnosis_id,
            recoverability=Recoverability.LIKELY_RECOVERABLE.value,
            candidate_strategy="retry",
            approved_strategy="retry",
            decision_status=DecisionStatus.APPROVED.value,
            rationale=[],
            scheduled_not_before=None,
            decision_engine_version=DECISION_ENGINE_VERSION,
        )
        session.add(conflict)
        await session.flush()
        await session.commit()

        duplicate = DecisionResultRow(
            case_id=case_id,
            diagnosis_id=diagnosis_id,
            recoverability=Recoverability.LIKELY_RECOVERABLE.value,
            candidate_strategy="retry",
            approved_strategy="retry",
            decision_status=DecisionStatus.APPROVED.value,
            rationale=[],
            scheduled_not_before=None,
            decision_engine_version=DECISION_ENGINE_VERSION,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()

        # 4. rollback -- this is what expires `diagnosis`'s attributes
        await session.rollback()

        # Sanity check that the defect's precondition is real: the ORM
        # object this test loaded in step 1 is indeed expired by the
        # rollback in step 4, confirming this test would have exercised
        # the bug had step 2 not captured diagnosis_id first.
        from sqlalchemy import inspect as sa_inspect

        assert sa_inspect(diagnosis).expired

        # 5. use the captured diagnosis_id (never diagnosis.id again)
        # 6. perform the existing-decision query
        existing = await _get_existing_decision(session, case_id, diagnosis_id)

        # 7. no MissingGreenlet -- reaching this line at all is the proof
        assert existing is not None
        assert existing.id == conflict.id


async def test_plain_expired_attribute_access_after_rollback_would_fail(
    client: AsyncClient,
) -> None:
    """Documents the exact failure this fix prevents: this is the OLD,
    unsafe pattern (`diagnosis.id` read directly after rollback), kept as
    a permanent negative-control regression so the root cause stays
    demonstrated even if the fix is ever accidentally reverted.
    """
    from sqlalchemy.exc import MissingGreenlet

    case_id = await _diagnosed_case(
        client,
        external_reference="d8-orm-neg",
        customer_external_id="cust-d8-orm-neg",
        failure_reason="insufficient_funds",
    )

    async with AsyncSessionLocal() as session:
        diagnosis = await get_latest_diagnosis(session, case_id)
        assert diagnosis is not None
        diagnosis_id = diagnosis.id

        conflict = DecisionResultRow(
            case_id=case_id,
            diagnosis_id=diagnosis_id,
            recoverability=Recoverability.LIKELY_RECOVERABLE.value,
            candidate_strategy="retry",
            approved_strategy="retry",
            decision_status=DecisionStatus.APPROVED.value,
            rationale=[],
            scheduled_not_before=None,
            decision_engine_version=DECISION_ENGINE_VERSION,
        )
        session.add(conflict)
        await session.flush()
        await session.commit()

        duplicate = DecisionResultRow(
            case_id=case_id,
            diagnosis_id=diagnosis_id,
            recoverability=Recoverability.LIKELY_RECOVERABLE.value,
            candidate_strategy="retry",
            approved_strategy="retry",
            decision_status=DecisionStatus.APPROVED.value,
            rationale=[],
            scheduled_not_before=None,
            decision_engine_version=DECISION_ENGINE_VERSION,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        # THE UNSAFE PATTERN: a plain (unawaited) attribute read on an
        # object rollback just expired. This is exactly what the fixed
        # decide_case no longer does.
        with pytest.raises(MissingGreenlet):
            await _get_existing_decision(session, case_id, diagnosis.id)


# --- Concurrency (KI-008-informed) -----------------------------------------


async def test_20_concurrent_identical_decide_requests_produce_exactly_one_decision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d8",
        customer_external_id="cust-d8",
        failure_reason="insufficient_funds",
    )

    async def _one() -> tuple[RecoveryCase, DecisionResultRow] | BaseException:
        async with AsyncSessionLocal() as session:
            try:
                return await decide_case(session, case_id)
            except BaseException as exc:  # noqa: BLE001 - captured for assertion, not swallowed
                return exc

    results = await asyncio.gather(*[_one() for _ in range(20)])

    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"no caller should see an unhandled error: {exceptions}"

    successes: list[tuple[RecoveryCase, DecisionResultRow]] = results  # type: ignore[assignment]
    decision_ids = {decision.id for _, decision in successes}
    assert decision_ids == {
        next(iter(decision_ids))
    }, "every caller must resolve to the same decision"

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(DecisionResultRow)
        .where(DecisionResultRow.case_id == case_id)
    )
    assert row_count == 1, "exactly one DecisionResult row, never a duplicate"

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.DECISION_PENDING)
    )
    assert (
        transition_count == 1
    ), "exactly one DIAGNOSED -> DECISION_PENDING transition, never a duplicate"

    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None
    assert case.state is RecoveryCaseState.DECISION_PENDING


# --- Provenance -------------------------------------------------------


async def test_decision_persists_full_provenance(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="d9",
        customer_external_id="cust-d9",
        failure_reason="insufficient_funds",
    )
    async with AsyncSessionLocal() as session:
        case, decision = await decide_case(session, case_id)

    stored = await db_session.get(DecisionResultRow, decision.id)
    assert stored is not None
    assert stored.case_id == case_id
    assert stored.diagnosis_id is not None
    assert stored.decision_engine_version == DECISION_ENGINE_VERSION
    assert stored.candidate_strategy == "retry"
    assert isinstance(stored.rationale, list) and len(stored.rationale) > 0
    assert all("rule_id" in entry for entry in stored.rationale)
    # No free-text AI reasoning is ever stored as rationale.
    assert not any("reasoning" in entry for entry in stored.rationale)


# --- Boundary: no AI, no execution, no payment mutation --------------------


async def test_decide_never_mutates_the_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment_resp = await _ingest(
        client,
        external_reference="d10",
        customer_external_id="cust-d10",
        failure_reason="insufficient_funds",
    )
    pid = payment_resp["payment_id"]
    before = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    snapshot = (before.status, before.amount, before.failure_reason)

    case_id = await _open_case(client, pid)
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200

    async with AsyncSessionLocal() as session:
        await decide_case(session, case_id)

    db_session.expire_all()
    after = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    assert (after.status, after.amount, after.failure_reason) == snapshot


async def test_decide_case_module_makes_no_ai_or_action_calls() -> None:
    # Structural guard: the service module must not import a reasoning
    # provider or any executor-shaped module.
    import app.decision.service as service_module

    source = service_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as f:
        text = f.read()
    for forbidden in (
        "app.ai.providers",
        "app.ai.diagnosis",
        "app.recovery.executor",
        "app.actions",
    ):
        assert forbidden not in text
