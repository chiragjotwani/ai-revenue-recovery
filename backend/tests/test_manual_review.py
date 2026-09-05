"""Phase 17: human-in-the-loop manual review resolution.

Real Postgres, real HTTP (project policy: no mocking the database) for
every path this suite can reach through the live pipeline. One
documented exception: an APPROVED (not ESCALATED) ``manual_review``
decision -- the only status that reaches ``schedule_action`` at all
(an ESCALATED decision is rejected with 409 before ever reaching
``execute_action`` -- see ``test_action_executor.py``'s own
``test_escalated_decision_cannot_be_scheduled``) -- can only occur today
via the policy engine's retry-cap-downgrade rule
(``app/decision/policy.py``), which is permanently unreachable through
the live HTTP pipeline because ``app.decision.service._RETRY_COUNT_PENDING_PHASE_6``
is hardcoded to ``0`` (no re-diagnosis loop exists yet -- a known,
documented gap, not something this suite works around silently). This
suite reaches that state the same way any test of currently-dead-but-
correct code must: drive a case to a real, persisted ``DecisionResult``
via the real diagnose+decide HTTP flow, then directly update that one
row's ``approved_strategy``/``decision_status`` columns to the
combination the policy engine would itself produce once retry_count is
ever live -- never inventing a shape the policy engine could not
itself produce.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionResult
from app.models.manual_review import ManualReviewResolution
from app.models.recovery import RecoveryCaseState, RecoveryCaseTransition

BASE = datetime(2026, 4, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-mr",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "mr@e.com"},
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


async def _open_case(client: AsyncClient, payment_id: str) -> uuid.UUID:
    r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def _pending_manual_review_case(
    client: AsyncClient, db_session: AsyncSession, *, external_reference: str
) -> uuid.UUID:
    """A case that has reached ``pending_manual_review`` -- see the module
    docstring for why the decision row is overridden directly rather than
    produced by the live policy engine.
    """
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=external_reference,
        )
    payment = await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        customer_external_id=external_reference,
    )
    case_id = await _open_case(client, payment["payment_id"])
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text
    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 200, r.text

    # Override the persisted decision to the one live-unreachable
    # combination the policy engine's own retry-cap-downgrade rule would
    # produce (approved_strategy=manual_review, decision_status=approved)
    # -- see the module docstring.
    await db_session.execute(
        update(DecisionResult)
        .where(DecisionResult.case_id == case_id)
        .values(approved_strategy="manual_review", decision_status="approved")
    )
    await db_session.commit()

    r = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert r.status_code == 200, r.text
    r = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert r.status_code == 200, r.text

    return case_id


# --- A. Reaching pending_manual_review -----------------------------------------


async def test_manual_review_action_blocks_case_in_pending_manual_review(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr1")

    case = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert case["state"] == "pending_manual_review"
    assert case["action"]["status"] == "executed"
    assert case["action"]["executions"][0]["outcome"] == "no_side_effect_required"
    assert case["manual_review_resolution"] is None


async def test_pending_manual_review_case_has_no_resulting_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A blocked manual-review case must never look like a recovery --
    nothing executed on any external system, no evidence was created.
    """
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr2")
    case = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert case["action"]["executions"][0]["resulting_payment_id"] is None
    assert case["outcome"] is None


# --- B. Resolution --------------------------------------------------------------


async def test_resolve_as_abandoned(client: AsyncClient, db_session: AsyncSession) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr3")

    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "abandoned", "note": "customer confirmed fraud, closing case"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolution"] == "abandoned"
    assert body["case_id"] == str(case_id)

    case = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert case["state"] == "abandoned"
    assert case["manual_review_resolution"]["resolution"] == "abandoned"


async def test_resolve_as_failed(client: AsyncClient, db_session: AsyncSession) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr4")

    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "failed", "note": "operator attempted manual outreach, no response"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["resolution"] == "failed"

    case = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert case["state"] == "failed"


async def test_resolve_records_note_and_actor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr5")
    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "abandoned", "note": "a specific operator reason"},
    )
    body = r.json()
    assert body["note"] == "a specific operator reason"
    assert body["actor"] == "operator:resolve_manual_review"


async def test_resolve_requires_a_nonempty_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr6")
    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "abandoned", "note": ""},
    )
    assert r.status_code == 422


async def test_resolve_rejects_recovered_as_a_resolution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The only two resolutions this endpoint's schema accepts are
    abandoned/failed -- recovered is not a valid enum member, so this is a
    422 schema-validation failure, not a reachable business rule (there is
    no code path that would let a human fabricate recovered evidence).
    """
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr7")
    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "recovered", "note": "should be rejected"},
    )
    assert r.status_code == 422


# --- C. Conflicts -----------------------------------------------------------


async def test_resolve_unknown_case_is_404(client: AsyncClient) -> None:
    r = await client.post(
        f"/recovery/cases/{uuid.uuid4()}/resolve-manual-review",
        json={"resolution": "abandoned", "note": "n/a"},
    )
    assert r.status_code == 404


async def test_resolve_before_pending_manual_review_is_409(client: AsyncClient) -> None:
    """A case that never escalated (e.g. still decision_pending) cannot
    have its manual review resolved.
    """
    payment = await _ingest_one(
        client,
        idempotency_key="mr8-f",
        external_reference="mr8-f",
        occurred_at=BASE,
        customer_external_id="cust-mr8",
    )
    case_id = await _open_case(client, payment["payment_id"])
    r = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "abandoned", "note": "n/a"},
    )
    assert r.status_code == 409


async def test_double_resolve_is_409(client: AsyncClient, db_session: AsyncSession) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr9")
    first = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "abandoned", "note": "first resolution"},
    )
    assert first.status_code == 200
    second = await client.post(
        f"/recovery/cases/{case_id}/resolve-manual-review",
        json={"resolution": "failed", "note": "attempted second resolution"},
    )
    assert second.status_code == 409

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(ManualReviewResolution)
        .where(ManualReviewResolution.case_id == case_id)
    )
    assert row_count == 1


async def test_concurrent_resolve_requests_produce_exactly_one_resolution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _pending_manual_review_case(client, db_session, external_reference="mr10")

    responses = await asyncio.gather(
        *[
            client.post(
                f"/recovery/cases/{case_id}/resolve-manual-review",
                json={"resolution": "abandoned", "note": f"attempt {i}"},
            )
            for i in range(20)
        ]
    )
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1, statuses
    assert statuses.count(409) == 19, statuses

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(ManualReviewResolution)
        .where(ManualReviewResolution.case_id == case_id)
    )
    assert row_count == 1

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.ABANDONED)
    )
    assert transition_count == 1


# --- D. State machine ---------------------------------------------------------


def test_state_machine_has_no_edge_back_from_pending_manual_review_to_decision_pending() -> None:
    """Structural guarantee, not just a convention: this phase never
    reopens a full re-decision loop (owner-scoped out).
    """
    from app.recovery.state_machine import LEGAL_TRANSITIONS

    assert LEGAL_TRANSITIONS[RecoveryCaseState.PENDING_MANUAL_REVIEW] == frozenset(
        {RecoveryCaseState.ABANDONED, RecoveryCaseState.FAILED}
    )


def test_pending_manual_review_is_not_a_terminal_state() -> None:
    from app.recovery.state_machine import TERMINAL_STATES

    assert RecoveryCaseState.PENDING_MANUAL_REVIEW not in TERMINAL_STATES
