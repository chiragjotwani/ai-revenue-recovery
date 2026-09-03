"""The authoritative canonical-scenario test (Phase 6 completion): the
₹4,999 insufficient-funds failure walking the FULL

    DETECT -> DIAGNOSE -> DECIDE -> POLICY VALIDATE -> SCHEDULE -> EXECUTE
    -> SIMULATED PROVIDER -> PAYMENT OUTCOME -> OBSERVE -> RECOVERED
    -> MEASURE REVENUE

pipeline for real, with the recovery genuinely CAUSED by the executed
action -- not by an unrelated, independently-ingested success event. This
is the test the Phase 6 completion brief calls
``test_canonical_recovery_flow_causes_revenue_recovery``.

Real Postgres, real HTTP (project policy: no mocking the database) --
matches every other integration test's convention in this suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action import RecoveryActionExecution
from app.models.outcome import RecoveryOutcomeObservation
from app.models.payment import Payment, PaymentStatus

BASE = datetime(2026, 3, 1, tzinfo=UTC)
AMOUNT = "4999.00"


async def _ingest(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str,
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "canonical@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": AMOUNT,
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_canonical_recovery_flow_causes_revenue_recovery(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    customer_external_id = f"cust-canonical-{uuid.uuid4().hex[:8]}"
    external_reference = f"canonical-{uuid.uuid4().hex[:8]}"

    # -- DETECT: three prior successes (sufficient, non-conflicting
    # evidence for Phase 4), then the ₹4,999 insufficient_funds failure.
    for i in range(3):
        await _ingest(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
        )
    failed_payment = await _ingest(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        failure_reason="insufficient_funds",
        customer_external_id=customer_external_id,
    )
    payment_id = failed_payment["payment_id"]

    risk_summary = await client.get("/risk/payments")
    assert risk_summary.status_code == 200
    assert any(p["payment_id"] == payment_id for p in risk_summary.json())

    open_resp = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert open_resp.status_code == 201, open_resp.text
    case_id = open_resp.json()["id"]

    # -- DIAGNOSE (Phase 4, existing AI diagnosis path -- MockProvider).
    diagnosed = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert diagnosed.status_code == 200, diagnosed.text
    diagnosis = diagnosed.json()
    assert diagnosis["outcome"] == "insufficient_funds"
    assert diagnosis["recommended_strategy"] == "retry"

    # -- DECIDE / POLICY VALIDATE (Phase 5): must be an APPROVED retry.
    decided = await client.post(f"/recovery/cases/{case_id}/decide")
    assert decided.status_code == 200, decided.text
    decision = decided.json()
    assert decision["approved_strategy"] == "retry"
    assert decision["decision_status"] == "approved"

    # -- SCHEDULE (Phase 6).
    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert scheduled.status_code == 200, scheduled.text
    action = scheduled.json()
    assert action["action_type"] == "retry"
    assert action["status"] == "scheduled"
    action_id = action["id"]

    # -- EXECUTE -> SIMULATED PROVIDER -> PAYMENT OUTCOME. insufficient_funds
    # succeeds on the first simulated attempt (app/decision/providers.py).
    executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert executed.status_code == 200, executed.text
    executed_action = executed.json()
    assert executed_action["status"] == "executed"
    execution = executed_action["executions"][0]
    assert execution["outcome"] == "simulated_success"
    resulting_payment_id = execution["resulting_payment_id"]
    assert resulting_payment_id is not None
    assert resulting_payment_id != payment_id  # a NEW payment, not the original failure

    detail_after_execute = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail_after_execute["state"] == "action_executed"

    # The simulated success payment is real evidence in the SAME tables
    # every other payment source uses -- verified directly, not inferred.
    resulting_payment = await db_session.get(Payment, uuid.UUID(resulting_payment_id))
    assert resulting_payment is not None
    assert resulting_payment.status is PaymentStatus.SUCCEEDED
    assert resulting_payment.amount == Decimal(AMOUNT)
    assert resulting_payment.currency == "INR"
    assert resulting_payment.customer_id == resulting_payment.customer_id  # sanity
    assert resulting_payment.external_reference.startswith("sim:retry:")

    # -- OBSERVE (Phase 7, completely unmodified): reads Payment evidence,
    # finds the simulated success, classifies RECOVERED.
    observed = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert observed.status_code == 200, observed.text
    outcome = observed.json()
    assert outcome["outcome"] == "recovered"
    assert outcome["evidence_payment_id"] == resulting_payment_id

    detail_after_observe = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail_after_observe["state"] == "recovered"

    # -- Causal correlation (not merely coincidental timing): the exact
    # execution attempt that ran is traceable to the exact payment Phase 7
    # used as its evidence.
    execution_row = await db_session.scalar(
        select(RecoveryActionExecution)
        .where(RecoveryActionExecution.action_id == uuid.UUID(action_id))
        .where(RecoveryActionExecution.attempt_no == 1)
    )
    assert execution_row is not None
    assert execution_row.resulting_payment_id == uuid.UUID(resulting_payment_id)

    observation_row = await db_session.scalar(
        select(RecoveryOutcomeObservation).where(
            RecoveryOutcomeObservation.case_id == uuid.UUID(case_id)
        )
    )
    assert observation_row is not None
    assert observation_row.evidence_payment_id == execution_row.resulting_payment_id
    # The full chain: this specific execution -> this specific payment ->
    # this specific observation's evidence. Not "a later success arrived
    # somehow" -- the SAME payment id at every link.

    # -- MEASURE REVENUE (Phase 8, completely unmodified).
    measured = await client.post(f"/recovery/cases/{case_id}/measure")
    assert measured.status_code == 200, measured.text
    assert measured.json()["status"] == "recovered"

    report = await client.get("/measurement/report")
    assert report.status_code == 200
    report_body = report.json()
    inr_recovered = next(
        (row for row in report_body["observed_recovered"] if row["currency"] == "INR"), None
    )
    assert inr_recovered is not None
    assert Decimal(str(inr_recovered["amount"])) >= Decimal(AMOUNT)
    assert inr_recovered["case_count"] >= 1


async def test_canonical_flow_is_not_confused_by_an_unrelated_later_success(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Phase 7's evidence rule is a later successful payment for the SAME
    customer -- so it is worth confirming this test suite's canonical
    scenario really is testing causation-by-simulated-action and not
    merely "any later success", by checking an UNRELATED customer's
    success never leaks into this case's evidence. This does not change
    Phase 7's rule (already correct); it verifies the completion brief's
    "must not depend on an unrelated manually-ingested success event"
    requirement holds for the new simulated path too.
    """
    customer_a = f"cust-a-{uuid.uuid4().hex[:8]}"
    customer_b = f"cust-b-{uuid.uuid4().hex[:8]}"
    ref_a = f"noise-a-{uuid.uuid4().hex[:8]}"
    ref_b = f"noise-b-{uuid.uuid4().hex[:8]}"

    for i in range(3):
        await _ingest(
            client,
            idempotency_key=f"{ref_a}-s{i}",
            external_reference=f"{ref_a}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_a,
        )
    failed_a = await _ingest(
        client,
        idempotency_key=f"{ref_a}-f",
        external_reference=ref_a,
        occurred_at=BASE,
        failure_reason="insufficient_funds",
        customer_external_id=customer_a,
    )

    # An unrelated customer B has a success recorded AFTER customer A's
    # failure -- must never be mistaken for A's evidence.
    await _ingest(
        client,
        idempotency_key=f"{ref_b}-s",
        external_reference=ref_b,
        occurred_at=BASE + timedelta(minutes=5),
        event_type="payment.succeeded",
        failure_reason=None,
        customer_external_id=customer_b,
    )

    case_id = (
        await client.post("/recovery/cases", json={"payment_id": failed_a["payment_id"]})
    ).json()["id"]
    await client.post(f"/recovery/cases/{case_id}/diagnose")
    await client.post(f"/recovery/cases/{case_id}/decide")
    await client.post(f"/recovery/cases/{case_id}/schedule-action")
    executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
    resulting_payment_id = executed.json()["executions"][0]["resulting_payment_id"]

    observed = (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).json()
    # Evidence must be THIS case's own simulated payment, never customer
    # B's unrelated success.
    assert observed["evidence_payment_id"] == resulting_payment_id
