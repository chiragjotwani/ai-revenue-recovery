"""Phase 6: integration tests for the action executor
(``app.decision.actions``) and its API surface (``POST
/recovery/cases/{id}/schedule-action`` / ``.../execute-action``).

Real Postgres, real HTTP (project policy: no mocking the database).
Mirrors ``test_decision_api.py``'s conventions.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.actions import execute_action, schedule_action
from app.models.action import RecoveryAction, RecoveryActionExecution
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
    customer_external_id: str = "cust-act",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "act@e.com"},
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


async def _decided_case(
    client: AsyncClient,
    *,
    external_reference: str,
    customer_external_id: str,
    failure_reason: str = "insufficient_funds",
) -> uuid.UUID:
    """A case decided against a fresh failure with 3 historical successes
    (sufficient, non-conflicting evidence) -- mirrors test_decision_api.py.
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
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text
    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 200, r.text
    return case_id


async def _already_paid_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str
) -> uuid.UUID:
    """A case whose customer paid successfully *after* the failure being
    decided -- decide_case's already_paid short-circuit -> approved
    no_action.
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
        failure_reason="insufficient_funds",
        customer_external_id=customer_external_id,
    )
    await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-later-success",
        external_reference=f"{external_reference}-later-success",
        occurred_at=BASE + timedelta(hours=2),
        event_type="payment.succeeded",
        failure_reason=None,
        customer_external_id=customer_external_id,
    )
    case_id = await _open_case(client, payment["payment_id"])
    r = await client.post(f"/recovery/cases/{case_id}/diagnose")
    assert r.status_code == 200, r.text
    r = await client.post(f"/recovery/cases/{case_id}/decide")
    assert r.status_code == 200, r.text
    assert r.json()["approved_strategy"] == "no_action"
    return case_id


# --- A. Happy path -----------------------------------------------------------


async def test_schedule_then_execute_retry_action_happy_path(client: AsyncClient) -> None:
    """insufficient_funds is the canonical scenario's profile: the
    deterministic simulated provider succeeds on the very first attempt
    (app/decision/providers.py). See test_canonical_recovery_flow.py for
    the full DETECT..MEASURE walk this outcome feeds into.
    """
    case_id = await _decided_case(client, external_reference="b1", customer_external_id="cust-b1")

    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert scheduled.status_code == 200, scheduled.text
    action = scheduled.json()
    assert action["action_type"] == "retry"
    assert action["status"] == "scheduled"

    executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "executed"
    assert len(body["executions"]) == 1
    assert body["executions"][0]["attempt_no"] == 1
    assert body["executions"][0]["outcome"] == "simulated_success"
    assert body["executions"][0]["idempotency_key"] == f"arr:{case_id}:retry:1"
    assert body["executions"][0]["resulting_payment_id"] is not None
    assert body["executions"][0]["simulated_reference"].startswith("sim:retry:")

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "action_executed"
    assert detail["action"]["action_type"] == "retry"


# --- E. Simulated execution: multi-attempt / permanent failure / cap -----------


async def test_retry_succeeds_only_after_a_second_attempt(client: AsyncClient) -> None:
    """do_not_honor's simulated profile is [temporary_failure, success]."""
    case_id = await _decided_case(
        client,
        external_reference="b11",
        customer_external_id="cust-b11",
        failure_reason="do_not_honor",
    )
    await client.post(f"/recovery/cases/{case_id}/schedule-action")

    first = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "scheduled"  # not yet terminal
    assert len(first_body["executions"]) == 1
    assert first_body["executions"][0]["outcome"] == "simulated_temporary_failure"
    assert first_body["executions"][0]["resulting_payment_id"] is None

    detail_mid = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail_mid["state"] == "action_scheduled"

    second = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["status"] == "executed"
    assert len(second_body["executions"]) == 2
    assert second_body["executions"][1]["attempt_no"] == 2
    assert second_body["executions"][1]["outcome"] == "simulated_success"
    assert second_body["executions"][1]["resulting_payment_id"] is not None

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "action_executed"


async def test_retry_permanent_failure_never_creates_payment_evidence(
    client: AsyncClient,
) -> None:
    """processing_error's simulated profile is [permanent_failure]."""
    case_id = await _decided_case(
        client,
        external_reference="b12",
        customer_external_id="cust-b12",
        failure_reason="processing_error",
    )
    await client.post(f"/recovery/cases/{case_id}/schedule-action")

    executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    # RecoveryAction.status is "executed" (the execution PROCESS
    # completed) regardless of success -- whether it succeeded is on the
    # execution's own `outcome`, not the action's `status` (this is what
    # keeps ACTION_EXECUTED -> OBSERVING's existing precondition,
    # app.recovery.preconditions._requires_executed_action, satisfied
    # without Phase 7 needing any change).
    assert body["status"] == "executed"
    assert len(body["executions"]) == 1
    assert body["executions"][0]["outcome"] == "simulated_permanent_failure"
    assert body["executions"][0]["resulting_payment_id"] is None

    # The case still advances -- the execution *process* completed, even
    # though it did not succeed. Phase 7 will separately observe no
    # recovery evidence for it.
    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["state"] == "action_executed"

    # Idempotent replay: calling execute-action again on a terminal action
    # must never attempt a new attempt.
    replay = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert replay.status_code == 200
    assert len(replay.json()["executions"]) == 1


async def test_retry_cap_is_never_exceeded(client: AsyncClient) -> None:
    """card_not_supported has no defined simulated profile -- it always
    reports a temporary failure (app/decision/providers.py's default
    profile) -- so the bounded retry cap (RETRY_CAP=3) must stop it,
    never retrying indefinitely, via the payment-link executor
    (RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE).
    """
    case_id = await _decided_case(
        client,
        external_reference="b13",
        customer_external_id="cust-b13",
        failure_reason="card_not_supported",
    )
    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert scheduled.json()["action_type"] == "request_payment_method_update"

    for expected_attempts in (1, 2, 3):
        r = await client.post(f"/recovery/cases/{case_id}/execute-action")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["executions"]) == expected_attempts
        assert body["executions"][-1]["outcome"] == "simulated_temporary_failure"
        if expected_attempts < 3:
            assert body["status"] == "scheduled"
        else:
            # Cap exhausted: RecoveryAction.status becomes "executed" (the
            # process completed) even though every attempt failed -- see
            # the "executed means completed, not succeeded" note above.
            assert body["status"] == "executed"

    # A 4th call must not create a 4th attempt -- the cap is exhausted,
    # and this is an idempotent replay of the final state.
    fourth = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert fourth.status_code == 200
    assert len(fourth.json()["executions"]) == 3
    assert fourth.json()["status"] == "executed"


async def test_no_action_strategy_is_a_safe_first_class_outcome(client: AsyncClient) -> None:
    case_id = await _already_paid_case(
        client, external_reference="b2", customer_external_id="cust-b2"
    )

    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["action_type"] == "no_action"

    executed = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "executed"
    assert body["executions"][0]["outcome"] == "no_side_effect_required"

    # Duplicate NO_ACTION execution must remain idempotent -- never an error.
    again = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert again.status_code == 200
    assert again.json()["executions"][0]["id"] == body["executions"][0]["id"]


# --- B. Policy boundary -------------------------------------------------------


async def test_escalated_decision_cannot_be_scheduled(client: AsyncClient) -> None:
    case_id = await _decided_case(
        client,
        external_reference="b3",
        customer_external_id="cust-b3",
        failure_reason="fraud_suspected",
    )

    decision = (await client.get(f"/recovery/cases/{case_id}")).json()["decision"]
    assert decision["decision_status"] == "escalated"

    r = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert r.status_code == 409


async def test_action_type_is_always_the_approved_strategy_never_the_candidate() -> None:
    """The executor's own signature is the guarantee: it accepts only a
    ``case_id`` and reads the decision's ``approved_strategy`` from the
    database -- no caller (human, API client, or a future AI-facing
    surface) can pass a strategy in directly. This is what makes "arbitrary
    AI reasoning cannot become an executable instruction" structurally
    true, not just conventionally true.
    """
    schedule_params = list(inspect.signature(schedule_action).parameters)
    execute_params = list(inspect.signature(execute_action).parameters)
    assert schedule_params == ["session", "case_id"]
    assert execute_params == ["session", "case_id"]


# --- C. State boundary --------------------------------------------------------


async def test_schedule_action_before_a_decision_exists_returns_500(client: AsyncClient) -> None:
    payment = await _ingest_one(
        client,
        idempotency_key="b4-f",
        external_reference="b4-f",
        occurred_at=BASE,
        customer_external_id="cust-b4",
    )
    case_id = await _open_case(client, payment["payment_id"])
    await client.post(f"/recovery/cases/{case_id}/diagnose")  # diagnosed, not yet decided

    r = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert r.status_code == 500


async def test_execute_action_before_scheduling_returns_500(client: AsyncClient) -> None:
    case_id = await _decided_case(client, external_reference="b5", customer_external_id="cust-b5")
    r = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert r.status_code == 500


# --- D. Idempotency ------------------------------------------------------------


async def test_duplicate_sequential_schedule_returns_the_same_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _decided_case(client, external_reference="b6", customer_external_id="cust-b6")

    first = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    second = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    row_count = await db_session.scalar(
        select(func.count()).select_from(RecoveryAction).where(RecoveryAction.case_id == case_id)
    )
    assert row_count == 1


async def test_concurrent_schedule_requests_produce_exactly_one_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _decided_case(client, external_reference="b7", customer_external_id="cust-b7")

    responses = await asyncio.gather(
        *[client.post(f"/recovery/cases/{case_id}/schedule-action") for _ in range(20)]
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1

    row_count = await db_session.scalar(
        select(func.count()).select_from(RecoveryAction).where(RecoveryAction.case_id == case_id)
    )
    assert row_count == 1

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.ACTION_SCHEDULED)
    )
    assert transition_count == 1


async def test_duplicate_sequential_execute_returns_the_same_execution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _decided_case(client, external_reference="b8", customer_external_id="cust-b8")
    await client.post(f"/recovery/cases/{case_id}/schedule-action")

    first = await client.post(f"/recovery/cases/{case_id}/execute-action")
    second = await client.post(f"/recovery/cases/{case_id}/execute-action")
    assert first.status_code == second.status_code == 200
    assert first.json()["executions"][0]["id"] == second.json()["executions"][0]["id"]

    action_id = uuid.UUID(first.json()["id"])
    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryActionExecution)
        .where(RecoveryActionExecution.action_id == action_id)
    )
    assert row_count == 1


async def test_concurrent_execute_requests_produce_exactly_one_execution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _decided_case(client, external_reference="b9", customer_external_id="cust-b9")
    scheduled = await client.post(f"/recovery/cases/{case_id}/schedule-action")
    action_id = uuid.UUID(scheduled.json()["id"])

    responses = await asyncio.gather(
        *[client.post(f"/recovery/cases/{case_id}/execute-action") for _ in range(20)]
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    execution_ids = {r.json()["executions"][0]["id"] for r in responses}
    assert len(execution_ids) == 1

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryActionExecution)
        .where(RecoveryActionExecution.action_id == action_id)
    )
    assert row_count == 1

    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(RecoveryCaseTransition)
        .where(RecoveryCaseTransition.case_id == case_id)
        .where(RecoveryCaseTransition.to_state == RecoveryCaseState.ACTION_EXECUTED)
    )
    assert transition_count == 1


# --- G. Persistence / audit ---------------------------------------------------


async def test_get_case_detail_exposes_action_and_execution_audit_trail(
    client: AsyncClient,
) -> None:
    case_id = await _decided_case(client, external_reference="b10", customer_external_id="cust-b10")
    await client.post(f"/recovery/cases/{case_id}/schedule-action")
    await client.post(f"/recovery/cases/{case_id}/execute-action")

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["action"]["decision_result_id"] == detail["decision"]["id"]
    assert detail["action"]["action_type"] == detail["decision"]["approved_strategy"]
    assert len(detail["action"]["executions"]) == 1

    history_states = [h["to_state"] for h in detail["history"]]
    assert history_states == [
        "detected",
        "diagnosing",
        "diagnosed",
        "decision_pending",
        "action_scheduled",
        "action_executed",
    ]


# --- Missing case / malformed input ------------------------------------------


async def test_schedule_action_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/schedule-action")
    assert r.status_code == 404


async def test_execute_action_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/execute-action")
    assert r.status_code == 404
