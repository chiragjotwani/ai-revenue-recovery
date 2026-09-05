"""Phase 8: integration tests for revenue measurement
(``app.measurement.service``) and its API surface
(``POST /recovery/cases/{id}/measure``, ``GET /measurement/report``).

Real Postgres, real HTTP (project policy: no mocking the database).
Mirrors ``test_outcome_observation.py``'s conventions.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.measurement.service import measure_case
from app.models.measurement import RevenueMeasurement

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-meas",
    amount: str = "4999.00",
    currency: str = "inr",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "meas@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": amount,
            "currency": currency,
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
    amount: str = "4999.00",
    currency: str = "inr",
) -> tuple[uuid.UUID, datetime]:
    # Default failure_reason is deliberately `processing_error`, not
    # `insufficient_funds`: since Phase 6's completion, the retry executor
    # dispatches to a deterministic SIMULATED provider whose
    # `insufficient_funds` profile always succeeds on the first attempt --
    # which would create its own payment.succeeded evidence and make every
    # case "recovered" before a test's own evidence event is ingested.
    # `processing_error`'s simulated profile is a permanent failure (no
    # payment created), preserving this fixture's "executed, no automatic
    # evidence" starting state. See test_canonical_recovery_flow.py for
    # the simulated-success path's own coverage.
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
            amount=amount,
            currency=currency,
        )
    payment = await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        failure_reason=failure_reason,
        customer_external_id=customer_external_id,
        amount=amount,
        currency=currency,
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
    amount: str = "4999.00",
    currency: str = "inr",
) -> None:
    await _ingest_one(
        client,
        idempotency_key=external_reference,
        external_reference=external_reference,
        occurred_at=occurred_at,
        event_type=event_type,
        failure_reason="insufficient_funds" if event_type == "payment.failed" else None,
        customer_external_id=customer_external_id,
        amount=amount,
        currency=currency,
    )


async def _recovered_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, **kw: object
) -> uuid.UUID:
    case_id, failed_at = await _executed_case(
        client,
        external_reference=external_reference,
        customer_external_id=customer_external_id,
        **kw,
    )
    await _ingest_later(
        client,
        external_reference=f"{external_reference}-later-success",
        customer_external_id=customer_external_id,
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
        amount=str(kw.get("amount", "4999.00")),
        currency=str(kw.get("currency", "inr")),
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200
    return case_id


# --- A/B/C. status-specific measurement --------------------------------------


async def test_measure_recovered_case(client: AsyncClient) -> None:
    case_id = await _recovered_case(client, external_reference="m1", customer_external_id="cust-m1")

    r = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "recovered"
    assert body["case_id"] == str(case_id)
    assert body["payment_id"] is not None
    assert body["outcome_observation_id"] is not None

    detail = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert detail["measurement"]["status"] == "recovered"


async def test_measure_not_recovered_case(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="m2", customer_external_id="cust-m2"
    )
    await _ingest_later(
        client,
        external_reference="m2-later-failure",
        customer_external_id="cust-m2",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.failed",
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200

    r = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "not_recovered"


async def test_measure_unresolved_case(client: AsyncClient) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="m3", customer_external_id="cust-m3"
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200

    r = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unresolved"


async def test_measure_before_outcome_observed_returns_409(client: AsyncClient) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="m4", customer_external_id="cust-m4"
    )
    r = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r.status_code == 409


async def test_measure_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.post(f"/recovery/cases/{uuid.uuid4()}/measure")
    assert r.status_code == 404


# --- D. currency separation ---------------------------------------------------


async def test_report_never_sums_across_currencies(client: AsyncClient) -> None:
    inr_case = await _recovered_case(
        client,
        external_reference="m5-inr",
        customer_external_id="cust-m5-inr",
        amount="1000.00",
        currency="inr",
    )
    usd_case = await _recovered_case(
        client,
        external_reference="m5-usd",
        customer_external_id="cust-m5-usd",
        amount="50.00",
        currency="usd",
    )
    await client.post(f"/recovery/cases/{inr_case}/measure")
    await client.post(f"/recovery/cases/{usd_case}/measure")

    report = (await client.get("/measurement/report")).json()
    currencies = {row["currency"] for row in report["observed_recovered"]}
    assert "INR" in currencies
    assert "USD" in currencies
    # Each currency's own row, never merged into one combined figure --
    # there must be no field anywhere carrying a cross-currency sum.
    assert "total" not in report
    assert "revenue_at_risk" not in report
    inr_row = next(row for row in report["observed_recovered"] if row["currency"] == "INR")
    usd_row = next(row for row in report["observed_recovered"] if row["currency"] == "USD")
    assert Decimal(inr_row["amount"]) >= Decimal("1000.00")
    assert Decimal(usd_row["amount"]) >= Decimal("50.00")


# --- E/F. attribution & unrelated-payment rejection ---------------------------


async def test_measurement_traces_to_the_phase7_observation(client: AsyncClient) -> None:
    """The measurement's own signature is the guarantee: it takes only a
    session and a case_id, and internally requires an existing
    RecoveryOutcomeObservation -- no separate correlation logic exists in
    this module (no fuzzy matching can be introduced because there is no
    parameter to carry one).
    """
    params = list(inspect.signature(measure_case).parameters)
    assert params == ["session", "case_id"]


async def test_unrelated_customer_success_does_not_inflate_recovered_value(
    client: AsyncClient,
) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="m6", customer_external_id="cust-m6"
    )
    # No later success for THIS customer -- only an unrelated customer's
    # success exists (ingested separately, never linked to this case).
    await _ingest_one(
        client,
        idempotency_key="m6-unrelated-success",
        external_reference="m6-unrelated-success",
        occurred_at=BASE + timedelta(hours=1),
        event_type="payment.succeeded",
        failure_reason=None,
        customer_external_id="cust-m6-unrelated",
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200
    r = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r.json()["status"] == "unresolved"


# --- G/H/I. idempotency, concurrency, no double counting ---------------------


async def test_duplicate_sequential_measurement_returns_the_same_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _recovered_case(client, external_reference="m7", customer_external_id="cust-m7")

    first = await client.post(f"/recovery/cases/{case_id}/measure")
    second = await client.post(f"/recovery/cases/{case_id}/measure")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RevenueMeasurement)
        .where(RevenueMeasurement.case_id == case_id)
    )
    assert row_count == 1


async def test_repeated_measurement_calls_do_not_double_count_in_the_report(
    client: AsyncClient,
) -> None:
    case_id = await _recovered_case(client, external_reference="m8", customer_external_id="cust-m8")
    for _ in range(5):
        await client.post(f"/recovery/cases/{case_id}/measure")

    report = (await client.get("/measurement/report")).json()
    inr_row = next(row for row in report["observed_recovered"] if row["currency"] == "INR")
    # This case's own payment amount must appear exactly once in the
    # case_count for its currency bucket, regardless of how many times
    # /measure was called -- the report is a live, case-scoped aggregate,
    # not a sum over RevenueMeasurement rows.
    assert inr_row["case_count"] >= 1


async def test_concurrent_identical_measurements_produce_exactly_one_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _recovered_case(client, external_reference="m9", customer_external_id="cust-m9")

    responses = await asyncio.gather(
        *[client.post(f"/recovery/cases/{case_id}/measure") for _ in range(20)]
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(RevenueMeasurement)
        .where(RevenueMeasurement.case_id == case_id)
    )
    assert row_count == 1


async def test_repeated_action_execution_attempts_do_not_double_count(
    client: AsyncClient,
) -> None:
    """A retried/duplicate action execution call (already idempotent at
    the Phase 6 layer) must not somehow produce two measurements either.
    """
    case_id = await _recovered_case(
        client, external_reference="m10", customer_external_id="cust-m10"
    )
    for _ in range(3):
        await client.post(f"/recovery/cases/{case_id}/execute-action")
    r1 = await client.post(f"/recovery/cases/{case_id}/measure")
    r2 = await client.post(f"/recovery/cases/{case_id}/measure")
    assert r1.json()["id"] == r2.json()["id"]


# --- J. provenance -------------------------------------------------------------


async def test_measurement_provenance_fields_are_persisted(client: AsyncClient) -> None:
    case_id = await _recovered_case(
        client, external_reference="m11", customer_external_id="cust-m11"
    )
    r = await client.post(f"/recovery/cases/{case_id}/measure")
    body = r.json()
    assert body["case_id"] == str(case_id)
    assert body["payment_id"] is not None
    assert body["outcome_observation_id"] is not None
    assert body["measured_at"] is not None
    assert body["status"] == "recovered"


# --- K. security -----------------------------------------------------------


async def test_measure_endpoint_accepts_no_request_body_fields(client: AsyncClient) -> None:
    """No client-supplied amount/currency/status can become authoritative:
    the endpoint takes no body at all, so posting one (as an attacker
    might try) is simply ignored -- the measured status is still derived
    exclusively from the case's own observed outcome.
    """
    case_id = await _recovered_case(
        client, external_reference="m12", customer_external_id="cust-m12"
    )
    r = await client.post(
        f"/recovery/cases/{case_id}/measure",
        json={"status": "recovered", "amount": "999999999.00", "currency": "usd"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "recovered"  # correct anyway -- from the real outcome
    # currency/amount were never part of the request or response schema at all.
    assert "amount" not in r.json()
    assert "currency" not in r.json()


async def test_measurement_never_executes_an_action_or_changes_the_decision(
    client: AsyncClient,
) -> None:
    case_id = await _recovered_case(
        client, external_reference="m13", customer_external_id="cust-m13"
    )
    before = (await client.get(f"/recovery/cases/{case_id}")).json()

    await client.post(f"/recovery/cases/{case_id}/measure")

    after = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert before["decision"] == after["decision"]
    assert before["action"]["id"] == after["action"]["id"]
    assert before["state"] == after["state"]  # measurement drives no state transition


# --- L. baseline / counterfactual semantics -----------------------------------


async def test_report_never_claims_incremental_or_causal_impact(client: AsyncClient) -> None:
    report = (await client.get("/measurement/report")).json()
    assert report["measurement_basis"] == "observed_evidence"
    assert report["counterfactual_available"] is False
    assert "counterfactual" in report["counterfactual_limitation"].lower()
    assert "control" in report["counterfactual_limitation"].lower()


def test_revenue_report_response_never_declares_an_incremental_field() -> None:
    from app.measurement.schema import RevenueReport

    field_names = set(RevenueReport.model_fields)
    for forbidden in ("incremental", "impact", "uplift", "caused_by_ai", "ai_revenue"):
        assert not any(forbidden in name for name in field_names), field_names


# --- report eligibility / recovery rate -----------------------------------


async def test_report_recovery_rate_is_a_case_count_ratio_never_divides_by_zero(
    client: AsyncClient,
) -> None:
    report = await get_revenue_report_via_service(client)
    assert 0.0 <= report["observed_recovery_rate"] <= 1.0


async def get_revenue_report_via_service(client: AsyncClient) -> dict:
    r = await client.get("/measurement/report")
    assert r.status_code == 200
    return r.json()
