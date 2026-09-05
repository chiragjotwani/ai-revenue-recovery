"""Phase 5F: integration tests for POST /recovery/cases/{id}/decide and the
decision fields exposed on GET /recovery/cases/{id}.

Real Postgres, real HTTP (project policy: no mocking the database). Mirrors
test_diagnosis_api.py's and test_decision_service.py's conventions.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionResult as DecisionResultRow
from app.models.payment import Payment
from app.models.recovery import RecoveryCase, RecoveryCaseState, RecoveryCaseTransition

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-api-dec",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": BASE.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "apidec@e.com"},
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


# --- 1/2. successful, approved decision ------------------------------------


async def test_decide_returns_200_and_an_approved_decision(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a1",
        customer_external_id="cust-a1",
        failure_reason="insufficient_funds",
    )

    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["decision_status"] == "approved"
    assert body["candidate_strategy"] == "retry"
    assert body["approved_strategy"] == "retry"
    assert body["recoverability"] == "likely_recoverable"
    assert body["case_id"] == str(case_id)
    assert body["diagnosis_id"] is not None
    assert body["decision_engine_version"] == "1"
    assert isinstance(body["rationale"], list) and len(body["rationale"]) > 0


# --- 3. escalated decision ---------------------------------------------------


async def test_decide_returns_200_and_an_escalated_decision(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a2",
        customer_external_id="cust-a2",
        failure_reason="fraud_suspected",
    )

    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["decision_status"] == "escalated"
    assert body["approved_strategy"] == "manual_review"
    assert body["recoverability"] == "not_recoverable_automatically"


# --- 4. rejected decision: not currently reachable (documented, not faked) -


def test_rejected_status_is_not_currently_producible_via_the_api() -> None:
    # Mirrors test_decision_service.py::test_rejected_and_superseded_are_not_
    # currently_reachable: every app.decision.policy.evaluate branch resolves
    # to APPROVED or ESCALATED today, so there is no scenario that makes the
    # API return "rejected". Documenting this rather than fabricating a test
    # for an unreachable path.
    from app.decision.schema import DecisionStatus

    assert DecisionStatus.REJECTED.value == "rejected"


# --- 5. duplicate sequential decision ---------------------------------------


async def test_duplicate_sequential_decide_returns_the_same_decision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a3",
        customer_external_id="cust-a3",
        failure_reason="insufficient_funds",
    )

    first = await client.post(f"/recovery/cases/{case_id}/decide")
    second = await client.post(f"/recovery/cases/{case_id}/decide")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(DecisionResultRow)
        .where(DecisionResultRow.case_id == case_id)
    )
    assert row_count == 1


# --- 6. concurrent decision requests -----------------------------------------


async def test_concurrent_decide_requests_produce_exactly_one_decision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a4",
        customer_external_id="cust-a4",
        failure_reason="insufficient_funds",
    )

    responses = await asyncio.gather(
        *[client.post(f"/recovery/cases/{case_id}/decide") for _ in range(20)]
    )

    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    decision_ids = {r.json()["id"] for r in responses}
    assert len(decision_ids) == 1, "every caller must resolve to the same decision"

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(DecisionResultRow)
        .where(DecisionResultRow.case_id == case_id)
    )
    assert row_count == 1

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.DECISION_PENDING)
    )
    assert transition_count == 1


# --- 7. missing case ---------------------------------------------------------


async def test_decide_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/decide")
    assert r.status_code == 404


# --- 8. invalid case state ---------------------------------------------------


async def test_decide_before_diagnosis_returns_409(client: AsyncClient) -> None:
    payment = await _ingest_one(
        client, idempotency_key="a5-f", external_reference="a5-f", customer_external_id="cust-a5"
    )
    case_id = await _open_case(client, payment["payment_id"])  # state: detected

    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 409


# --- 9. missing diagnosis: not reachable via the API without a diagnosis ----
#
# There is no client-triggerable way to reach `diagnosed` without a
# persisted Diagnosis (the diagnose endpoint is the only path there, and it
# always persists one first) -- this is exactly what the DIAGNOSING ->
# DIAGNOSED precondition already guarantees. NoDiagnosisToDecideError is a
# defensive, should-not-occur check; not exercised at the API layer.


# --- 10. malformed case ID ----------------------------------------------------


async def test_decide_malformed_case_id_returns_422(client: AsyncClient) -> None:
    r = await client.post("/recovery/cases/not-a-uuid/decide")
    assert r.status_code == 422


# --- 11. response schema -----------------------------------------------------


async def test_decision_response_schema_fields(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a6",
        customer_external_id="cust-a6",
        failure_reason="insufficient_funds",
    )
    r = await client.post(f"/recovery/cases/{case_id}/decide")
    body = r.json()

    for field in (
        "id",
        "case_id",
        "diagnosis_id",
        "recoverability",
        "candidate_strategy",
        "approved_strategy",
        "decision_status",
        "rationale",
        "scheduled_not_before",
        "decision_engine_version",
        "created_at",
    ):
        assert field in body, f"missing field: {field}"
    for entry in body["rationale"]:
        assert "rule_id" in entry
        assert "outcome" in entry
        assert "reasoning" not in entry  # no free-text AI reasoning ever


# --- 12/13. persistence correctness + provenance fields ----------------------


async def test_decide_persists_full_provenance(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a7",
        customer_external_id="cust-a7",
        failure_reason="insufficient_funds",
    )
    r = await client.post(f"/recovery/cases/{case_id}/decide")
    decision_id = uuid.UUID(r.json()["id"])

    stored = await db_session.get(DecisionResultRow, decision_id)
    assert stored is not None
    assert stored.case_id == case_id
    assert stored.diagnosis_id is not None
    assert stored.decision_engine_version == "1"

    case = await db_session.get(RecoveryCase, case_id)
    assert case is not None
    assert case.state is RecoveryCaseState.DECISION_PENDING


async def test_get_case_exposes_the_decision(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client,
        external_reference="a8",
        customer_external_id="cust-a8",
        failure_reason="insufficient_funds",
    )
    assert (await client.get(f"/recovery/cases/{case_id}")).json()["decision"] is None

    await client.post(f"/recovery/cases/{case_id}/decide")
    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["decision"] is not None
    assert detail["decision"]["decision_status"] == "approved"
    assert detail["state"] == "decision_pending"


# --- 14/15/16. no action execution, no payment/revenue mutation -------------


async def test_decide_never_mutates_the_payment_via_the_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payment_resp = await _ingest(
        client,
        external_reference="a9",
        customer_external_id="cust-a9",
        failure_reason="insufficient_funds",
    )
    pid = payment_resp["payment_id"]
    before = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    snapshot = (before.status, before.amount, before.failure_reason)

    case_id = await _open_case(client, pid)
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/decide")).status_code == 200

    db_session.expire_all()
    after = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    assert (after.status, after.amount, after.failure_reason) == snapshot


async def test_decide_endpoint_module_makes_no_action_or_ai_calls() -> None:
    # Structural guard, mirroring test_decide_case_module_makes_no_ai_or_
    # action_calls: the API layer must not import an executor-shaped module
    # itself either (it only calls decide_case, which already carries this
    # guarantee).
    import app.api.recovery as recovery_api

    source = recovery_api.__file__
    assert source is not None
    with open(source, encoding="utf-8") as f:
        text = f.read()
    for forbidden in ("app.recovery.executor", "app.actions"):
        assert forbidden not in text
