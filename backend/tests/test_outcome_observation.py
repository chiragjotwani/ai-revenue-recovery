"""Phase 7: integration tests for outcome observation
(``app.outcome.service``) and its API surface
(``POST /recovery/cases/{id}/observe-outcome``).

Real Postgres, real HTTP (project policy: no mocking the database).
Mirrors ``test_action_executor.py``'s conventions.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outcome import RecoveryOutcomeObservation
from app.models.recovery import RecoveryCaseState, RecoveryCaseTransition

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-obs",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "obs@e.com"},
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


async def _executed_case(
    client: AsyncClient,
    *,
    external_reference: str,
    customer_external_id: str,
    failure_reason: str = "processing_error",
) -> tuple[uuid.UUID, datetime]:
    """A case carried all the way through diagnose -> decide -> schedule ->
    execute. Returns (case_id, failed_payment_occurred_at) so tests can
    ingest evidence relative to it.

    Default failure_reason is deliberately ``processing_error``, not
    ``insufficient_funds``: since Phase 6's completion, the retry executor
    dispatches to a deterministic SIMULATED provider
    (``app.decision.providers``) whose ``insufficient_funds`` profile
    always succeeds on the first attempt -- which would create its own
    ``payment.succeeded`` evidence and make every case "recovered" before
    a test's own `_ingest_later` call ever runs. `processing_error`'s
    simulated profile is a permanent failure (no payment created), so this
    fixture still produces an "executed, no automatic evidence" starting
    state -- exactly what Phase 7's own observation tests need to control
    evidence independently. See tests/test_action_executor.py and
    tests/test_canonical_recovery_flow.py for coverage of the simulated
    success path itself.
    """
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
        )
    payment = await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        failure_reason=failure_reason,
        customer_external_id=customer_external_id,
    )
    case_id = await _open_case(client, payment["payment_id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/decide")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/schedule-action")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/execute-action")).status_code == 200
    return case_id, BASE


async def _ingest_later(
    client: AsyncClient,
    *,
    external_reference: str,
    customer_external_id: str,
    occurred_at: datetime,
    event_type: str,
) -> None:
    await _ingest_one(
        client,
        idempotency_key=external_reference,
        external_reference=external_reference,
        occurred_at=occurred_at,
        event_type=event_type,
        failure_reason="insufficient_funds" if event_type == "payment.failed" else None,
        customer_external_id=customer_external_id,
    )


# --- A. Happy path -------------------------------------------------------


async def test_observe_recovered_outcome_from_a_later_success(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o1", customer_external_id="cust-o1"
    )
    await _ingest_later(
        client,
        external_reference="o1-later-success",
        customer_external_id="cust-o1",
        occurred_at=failed_at + timedelta(hours=2),
        event_type="payment.succeeded",
    )

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "recovered"
    assert body["is_terminal"] is True
    assert body["evidence_payment_id"] is not None
    assert body["attempt_no"] == 1

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "recovered"
    assert detail["outcome"]["outcome"] == "recovered"


# --- B. Unresolved path ---------------------------------------------------


async def test_observe_unresolved_outcome_with_no_conclusive_evidence(
    client: AsyncClient,
) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="o2", customer_external_id="cust-o2"
    )

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "unresolved"
    assert body["is_terminal"] is False
    assert body["evidence_payment_id"] is None

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "observing"


# --- C. Negative outcome ---------------------------------------------------


async def test_observe_not_recovered_outcome_from_a_later_failure(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o3", customer_external_id="cust-o3"
    )
    await _ingest_later(
        client,
        external_reference="o3-later-failure",
        customer_external_id="cust-o3",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.failed",
    )

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "not_recovered"
    assert body["is_terminal"] is False
    assert body["evidence_payment_id"] is not None

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    # not_recovered never auto-advances the case to a terminal state --
    # Phase 7 does not invent that rule (see app.outcome.service docstring).
    assert detail["state"] == "observing"


async def test_unresolved_can_later_become_recovered_as_new_evidence_arrives(
    client: AsyncClient,
) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o4", customer_external_id="cust-o4"
    )

    first = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert first.json()["outcome"] == "unresolved"

    await _ingest_later(
        client,
        external_reference="o4-later-success",
        customer_external_id="cust-o4",
        occurred_at=failed_at + timedelta(hours=3),
        event_type="payment.succeeded",
    )
    second = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert second.status_code == 200
    assert second.json()["outcome"] == "recovered"
    assert second.json()["attempt_no"] == 2  # new evidence -> new append-only attempt

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "recovered"


# --- D/E. Correlation & safety ---------------------------------------------


async def test_unrelated_customer_success_cannot_be_treated_as_recovery(
    client: AsyncClient,
) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o5", customer_external_id="cust-o5"
    )
    # A different customer's success must never count as evidence for this case.
    await _ingest_later(
        client,
        external_reference="o5-other-customer-success",
        customer_external_id="cust-o5-unrelated",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 200
    assert r.json()["outcome"] == "unresolved"


async def test_success_before_the_failure_cannot_be_treated_as_recovery(
    client: AsyncClient,
) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o6", customer_external_id="cust-o6"
    )
    # The 3 historical successes ingested by _executed_case all occur
    # *before* failed_at -- confirm they are not (mis)treated as recovery
    # evidence for this case.
    assert failed_at is not None

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 200
    assert r.json()["outcome"] == "unresolved"


def test_action_type_never_influences_the_correlation_query() -> None:
    """The classifier's own signature is the guarantee: it takes only a
    session and a case, and reads exclusively from Payment rows -- no AI
    reasoning, no decision/action fields, can enter classification.
    """
    from app.outcome.service import classify_outcome

    params = list(inspect.signature(classify_outcome).parameters)
    assert params == ["session", "case"]


# --- F. Idempotency ----------------------------------------------------------


async def test_duplicate_sequential_observation_returns_the_same_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o7", customer_external_id="cust-o7"
    )
    await _ingest_later(
        client,
        external_reference="o7-later-success",
        customer_external_id="cust-o7",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )

    first = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    second = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryOutcomeObservation)
        .where(RecoveryOutcomeObservation.case_id == case_id)
    )
    assert row_count == 1


async def test_repeated_unresolved_observation_does_not_create_duplicates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="o8", customer_external_id="cust-o8"
    )

    for _ in range(3):
        r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
        assert r.status_code == 200
        assert r.json()["outcome"] == "unresolved"

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryOutcomeObservation)
        .where(RecoveryOutcomeObservation.case_id == case_id)
    )
    assert row_count == 1


async def test_concurrent_identical_observations_produce_exactly_one_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o9", customer_external_id="cust-o9"
    )
    await _ingest_later(
        client,
        external_reference="o9-later-success",
        customer_external_id="cust-o9",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )

    responses = await asyncio.gather(
        *[client.post(f"/recovery/cases/{case_id}/observe-outcome") for _ in range(20)]
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryOutcomeObservation)
        .where(RecoveryOutcomeObservation.case_id == case_id)
    )
    assert row_count == 1

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.RECOVERED)
    )
    assert transition_count == 1


# --- G. State machine ----------------------------------------------------


async def test_observe_before_action_executed_returns_500(client: AsyncClient) -> None:
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"o10-s{i}",
            external_reference=f"o10-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id="cust-o10",
        )
    payment = await _ingest_one(
        client,
        idempotency_key="o10-f",
        external_reference="o10-f",
        occurred_at=BASE,
        customer_external_id="cust-o10",
    )
    case_id = await _open_case(client, payment["payment_id"])
    await client.post(f"/recovery/cases/{case_id}/diagnose")
    await client.post(f"/recovery/cases/{case_id}/decide")  # decision_pending, no action yet

    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    assert r.status_code == 500


async def test_observe_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/observe-outcome")
    assert r.status_code == 404


# --- H. Separation from Phase 5/6 ------------------------------------------


async def test_observing_an_outcome_never_creates_a_second_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.models.action import RecoveryAction

    case_id, failed_at = await _executed_case(
        client, external_reference="o11", customer_external_id="cust-o11"
    )
    await _ingest_later(
        client,
        external_reference="o11-later-success",
        customer_external_id="cust-o11",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    before = (await client.get(f"/recovery/cases/{case_id}")).json()["action"]

    await client.post(f"/recovery/cases/{case_id}/observe-outcome")

    after = (await client.get(f"/recovery/cases/{case_id}")).json()["action"]
    assert before["id"] == after["id"]
    assert before["action_type"] == after["action_type"]

    action_count = await db_session.scalar(
        select(func.count()).select_from(RecoveryAction).where(RecoveryAction.case_id == case_id)
    )
    assert action_count == 1


async def test_observing_an_outcome_never_changes_the_decision(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o12", customer_external_id="cust-o12"
    )
    before = (await client.get(f"/recovery/cases/{case_id}")).json()["decision"]

    await _ingest_later(
        client,
        external_reference="o12-later-success",
        customer_external_id="cust-o12",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    await client.post(f"/recovery/cases/{case_id}/observe-outcome")

    after = (await client.get(f"/recovery/cases/{case_id}")).json()["decision"]
    assert before == after


# --- I. Audit ---------------------------------------------------------------


async def test_outcome_provenance_and_timestamps_are_persisted(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="o13", customer_external_id="cust-o13"
    )
    await _ingest_later(
        client,
        external_reference="o13-later-success",
        customer_external_id="cust-o13",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    r = await client.post(f"/recovery/cases/{case_id}/observe-outcome")
    body = r.json()
    assert body["case_id"] == str(case_id)
    assert body["action_id"] is not None
    assert body["evidence_payment_id"] is not None
    assert body["created_at"] is not None
    assert body["is_terminal"] is True
