"""Transition-precondition contract tests (Phase 4.1, Workstream C1/C2).

The precondition contract is declared for every forward transition and
*enforced* only where the backing artifact already exists and only when a
caller opts in. Phase 3's shape-only behaviour (the default) is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TransitionPreconditionError
from app.models.recovery import (
    RecoveryCase,
    RecoveryCaseState,
    RecoveryCaseTransition,
)
from app.recovery import preconditions, service
from app.recovery.state_machine import LEGAL_TRANSITIONS

_S = RecoveryCaseState

_HAPPY_PATH_EDGES = [
    (_S.DETECTED, _S.DIAGNOSING),
    (_S.DIAGNOSING, _S.DIAGNOSED),
    (_S.DIAGNOSED, _S.DECISION_PENDING),
    (_S.DECISION_PENDING, _S.ACTION_SCHEDULED),
    (_S.ACTION_SCHEDULED, _S.ACTION_EXECUTED),
    (_S.ACTION_EXECUTED, _S.OBSERVING),
    (_S.OBSERVING, _S.RECOVERED),
]


def test_every_forward_edge_has_a_declared_precondition() -> None:
    for edge in _HAPPY_PATH_EDGES:
        assert (
            edge in preconditions.TRANSITION_PRECONDITIONS
        ), f"{edge} has no declared precondition"


def test_precondition_keys_are_all_legal_transitions() -> None:
    for frm, to in preconditions.TRANSITION_PRECONDITIONS:
        assert to in LEGAL_TRANSITIONS[frm], f"{frm}->{to} is declared but not a legal edge"


def test_abandon_edge_has_no_precondition() -> None:
    # Abandoning a case never needs an artifact.
    assert (_S.DETECTED, _S.ABANDONED) not in preconditions.TRANSITION_PRECONDITIONS


async def _seed_failed_payment_and_case(client: AsyncClient) -> str:
    payload = {
        "idempotency_key": "pc-1",
        "event_type": "payment.failed",
        "source": "test",
        "occurred_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": "pc-cust", "email": "pc@e.com"},
        "payment": {
            "external_reference": "pc-1",
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": "insufficient_funds",
        },
    }
    pid = (await client.post("/events", json=payload)).json()["payment_id"]
    case = (await client.post("/recovery/cases", json={"payment_id": pid})).json()
    return str(case["id"])


async def test_check_flags_missing_diagnosis_and_passes_once_one_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _seed_failed_payment_and_case(client)
    await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": "diagnosing"})
    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None

    unmet = await preconditions.check(db_session, case, _S.DIAGNOSED)
    assert unmet is not None and "diagnosis" in unmet

    # Run the real Phase 4 diagnosis, which persists a Diagnosis row.
    await client.post(f"/recovery/cases/{case_id}/diagnose")
    await db_session.refresh(case)
    # Case is 'diagnosed' now; check the precondition as if re-entering.
    case.state = _S.DIAGNOSING
    assert await preconditions.check(db_session, case, _S.DIAGNOSED) is None


async def test_enforced_transition_without_diagnosis_raises_and_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _seed_failed_payment_and_case(client)
    await service.transition_case(db_session, case_id, _S.DIAGNOSING, actor="test")
    before = await db_session.scalar(select(func.count()).select_from(RecoveryCaseTransition))

    with pytest.raises(TransitionPreconditionError):
        await service.transition_case(
            db_session, case_id, _S.DIAGNOSED, actor="test", enforce_preconditions=True
        )

    after = await db_session.scalar(select(func.count()).select_from(RecoveryCaseTransition))
    assert after == before, "a blocked precondition must not write a transition row"
    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None and case.state is _S.DIAGNOSING


async def test_default_behaviour_is_unchanged_phase3_shape_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # enforce_preconditions defaults to False: the manual Phase 3 path still
    # drives states with no artifact, exactly as before.
    case_id = await _seed_failed_payment_and_case(client)
    for st in ("diagnosing", "diagnosed", "decision_pending"):
        r = await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": st})
        assert r.status_code == 200, r.text


async def test_diagnose_service_still_advances_case(client: AsyncClient) -> None:
    # Regression: the real Phase 4 flow (create diagnosis, then advance) is
    # untouched by the precondition module.
    case_id = await _seed_failed_payment_and_case(client)
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200
    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "diagnosed"
    assert detail["diagnosis"] is not None


# --- Phase 5D: DIAGNOSED -> DECISION_PENDING and DECISION_PENDING ->
# ACTION_SCHEDULED preconditions ---------------------------------------------


async def test_check_flags_missing_decision_result_and_passes_once_one_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.decision.service import decide_case

    case_id = await _seed_failed_payment_and_case(client)
    await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": "diagnosing"})
    await client.post(f"/recovery/cases/{case_id}/diagnose")
    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None
    assert case.state is _S.DIAGNOSED

    unmet = await preconditions.check(db_session, case, _S.DECISION_PENDING)
    assert unmet is not None and "DecisionResult" in unmet

    await decide_case(db_session, case_id)
    await db_session.refresh(case)
    assert case.state is _S.DECISION_PENDING

    # Re-check as if re-entering the DIAGNOSED -> DECISION_PENDING edge:
    # the DecisionResult now exists, so the precondition is met.
    case.state = _S.DIAGNOSED
    assert await preconditions.check(db_session, case, _S.DECISION_PENDING) is None


async def test_decide_case_enforces_the_precondition_it_declares(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Direct proof that decide_case's own DIAGNOSED -> DECISION_PENDING
    # transition now runs with enforce_preconditions=True (Phase 5D): a
    # manual attempt at the same transition, on a case with no
    # DecisionResult yet, is blocked exactly like the Phase 4.1 diagnosis
    # precondition is.
    case_id = await _seed_failed_payment_and_case(client)
    await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": "diagnosing"})
    await client.post(f"/recovery/cases/{case_id}/diagnose")

    with pytest.raises(TransitionPreconditionError):
        await service.transition_case(
            db_session, case_id, _S.DECISION_PENDING, actor="test", enforce_preconditions=True
        )

    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None and case.state is _S.DIAGNOSED


async def test_check_flags_missing_decision_result_for_action_scheduled(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Manually drive DIAGNOSED -> DECISION_PENDING via the raw Phase 3
    # shape-only endpoint (enforce_preconditions defaults to False there),
    # bypassing decide_case entirely, to construct a case that has reached
    # DECISION_PENDING with no DecisionResult -- the state this
    # precondition exists to catch.
    case_id = await _seed_failed_payment_and_case(client)
    await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": "diagnosing"})
    await client.post(f"/recovery/cases/{case_id}/diagnose")
    r = await client.post(
        f"/recovery/cases/{case_id}/transitions", json={"to_state": "decision_pending"}
    )
    assert r.status_code == 200, r.text

    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None and case.state is _S.DECISION_PENDING

    unmet = await preconditions.check(db_session, case, _S.ACTION_SCHEDULED)
    assert unmet is not None and "DecisionResult" in unmet


async def test_check_flags_an_escalated_decision_for_action_scheduled(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.decision.service import decide_case

    # Fraud disposition always escalates (Phase 5B); sufficient payment
    # history so the escalation is attributable to the fraud rule, not to
    # sparse-evidence.
    case_id = await _seed_sufficient_evidence_case(client, "pc-fraud", "fraud_suspected")
    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None

    await decide_case(db_session, case_id)
    await db_session.refresh(case)
    assert case.state is _S.DECISION_PENDING

    unmet = await preconditions.check(db_session, case, _S.ACTION_SCHEDULED)
    assert unmet is not None and "not 'approved'" in unmet


async def test_check_passes_for_action_scheduled_when_decision_is_approved(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.decision.service import decide_case

    case_id = await _seed_sufficient_evidence_case(client, "pc-approved", "insufficient_funds")
    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None

    await decide_case(db_session, case_id)
    await db_session.refresh(case)
    assert case.state is _S.DECISION_PENDING

    assert await preconditions.check(db_session, case, _S.ACTION_SCHEDULED) is None


async def _seed_sufficient_evidence_case(
    client: AsyncClient, key_prefix: str, failure_reason: str
) -> str:
    """3 successes then a failure for one customer -- 'sufficient' evidence
    in the Phase 4 context builder, matching test_decision_service.py's
    canonical setup, so the decision policy's verdict for the failure
    reason under test is not overridden by a sparse-evidence escalation.
    """
    for i in range(3):
        payload = {
            "idempotency_key": f"{key_prefix}-s{i}",
            "event_type": "payment.succeeded",
            "source": "test",
            "occurred_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
            "customer": {"external_id": key_prefix, "email": f"{key_prefix}@e.com"},
            "payment": {
                "external_reference": f"{key_prefix}-s{i}",
                "amount": "4999.00",
                "currency": "inr",
                "failure_reason": None,
            },
        }
        await client.post("/events", json=payload)

    payload = {
        "idempotency_key": f"{key_prefix}-f",
        "event_type": "payment.failed",
        "source": "test",
        "occurred_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
        "customer": {"external_id": key_prefix, "email": f"{key_prefix}@e.com"},
        "payment": {
            "external_reference": f"{key_prefix}-f",
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    pid = (await client.post("/events", json=payload)).json()["payment_id"]
    case = (await client.post("/recovery/cases", json={"payment_id": pid})).json()
    case_id = case["id"]
    await client.post(f"/recovery/cases/{case_id}/transitions", json={"to_state": "diagnosing"})
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    return str(case_id)
