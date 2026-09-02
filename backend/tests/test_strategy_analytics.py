"""Phase 9: integration tests for strategy analytics
(``app.analytics.service``) and its API surface
(``GET /analytics/strategy-dataset``, ``GET /analytics/strategy-report``).

Deliberately scoped: this phase implements a historical strategy dataset
and deterministic strategy analytics only -- no ML model, no recovery
probability, no strategy optimization (see ``app/analytics/schema.py``'s
module docstring for why). These tests assert that scope boundary
explicitly, not just the arithmetic.

Real Postgres, real HTTP (project policy: no mocking the database).
Mirrors ``test_revenue_measurement.py``'s conventions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-strat",
    amount: str = "4999.00",
    currency: str = "inr",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "strat@e.com"},
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


async def _decided_case(
    client: AsyncClient,
    *,
    external_reference: str,
    customer_external_id: str,
    failure_reason: str = "insufficient_funds",
) -> tuple[uuid.UUID, datetime]:
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
    return case_id, BASE


async def _executed_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, **kw: object
) -> tuple[uuid.UUID, datetime]:
    case_id, failed_at = await _decided_case(
        client,
        external_reference=external_reference,
        customer_external_id=customer_external_id,
        **kw,
    )
    assert (await client.post(f"/recovery/cases/{case_id}/schedule-action")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/execute-action")).status_code == 200
    return case_id, failed_at


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


# --- historical strategy dataset -------------------------------------------


async def test_dataset_includes_executed_case_with_no_outcome_yet(client: AsyncClient) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="sa1", customer_external_id="cust-sa1"
    )
    rows = (await client.get("/analytics/strategy-dataset")).json()
    row = next(r for r in rows if r["case_id"] == str(case_id))
    assert row["strategy"] == "retry"
    assert row["outcome"] is None  # not yet observed
    assert row["currency"] == "INR"


async def test_dataset_excludes_escalated_decisions_with_no_action(client: AsyncClient) -> None:
    """An escalated/rejected decision never reaches scheduling, so it has
    no `strategy` -- it must not appear in the strategy dataset at all
    (never silently attributed a strategy it was never approved for).
    """
    case_id, _failed_at = await _decided_case(
        client,
        external_reference="sa2",
        customer_external_id="cust-sa2",
        failure_reason="fraud_suspected",
    )
    rows = (await client.get("/analytics/strategy-dataset")).json()
    assert all(r["case_id"] != str(case_id) for r in rows)


async def test_dataset_reflects_observed_outcome(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="sa3", customer_external_id="cust-sa3"
    )
    await _ingest_later(
        client,
        external_reference="sa3-later-success",
        customer_external_id="cust-sa3",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200

    rows = (await client.get("/analytics/strategy-dataset")).json()
    row = next(r for r in rows if r["case_id"] == str(case_id))
    assert row["outcome"] == "recovered"


# --- strategy analytics report ----------------------------------------------


async def test_report_computes_empirical_recovery_rate_by_strategy(client: AsyncClient) -> None:
    recovered_case, failed_at = await _executed_case(
        client, external_reference="sa4-a", customer_external_id="cust-sa4-a"
    )
    await _ingest_later(
        client,
        external_reference="sa4-a-later-success",
        customer_external_id="cust-sa4-a",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    assert (
        await client.post(f"/recovery/cases/{recovered_case}/observe-outcome")
    ).status_code == 200

    not_recovered_case, failed_at2 = await _executed_case(
        client, external_reference="sa4-b", customer_external_id="cust-sa4-b"
    )
    await _ingest_later(
        client,
        external_reference="sa4-b-later-failure",
        customer_external_id="cust-sa4-b",
        occurred_at=failed_at2 + timedelta(hours=1),
        event_type="payment.failed",
    )
    assert (
        await client.post(f"/recovery/cases/{not_recovered_case}/observe-outcome")
    ).status_code == 200

    report = (await client.get("/analytics/strategy-report")).json()
    retry_stat = next(s for s in report["by_strategy"] if s["key"] == "retry")
    assert retry_stat["recovered_count"] >= 1
    assert retry_stat["not_recovered_count"] >= 1
    assert retry_stat["observed_count"] >= 2
    assert 0.0 <= retry_stat["empirical_recovery_rate"] <= 1.0


async def test_report_never_divides_by_zero_when_no_observations_exist(
    client: AsyncClient,
) -> None:
    await _executed_case(client, external_reference="sa5", customer_external_id="cust-sa5")
    report = (await client.get("/analytics/strategy-report")).json()
    retry_stat = next(s for s in report["by_strategy"] if s["key"] == "retry")
    # This case alone has no observation yet -- if it were the only one,
    # empirical_recovery_rate must be null, never a fabricated 0.0 or crash.
    if retry_stat["observed_count"] == 0:
        assert retry_stat["empirical_recovery_rate"] is None


async def test_report_flags_low_sample_size_honestly(client: AsyncClient) -> None:
    case_id, failed_at = await _executed_case(
        client, external_reference="sa6", customer_external_id="cust-sa6"
    )
    await _ingest_later(
        client,
        external_reference="sa6-later-success",
        customer_external_id="cust-sa6",
        occurred_at=failed_at + timedelta(hours=1),
        event_type="payment.succeeded",
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200

    report = (await client.get("/analytics/strategy-report")).json()
    disposition_stat = next(
        s for s in report["by_disposition"] if s["key"] == "retriable_transient"
    )
    assert disposition_stat["low_sample"] is True  # far fewer than LOW_SAMPLE_THRESHOLD=5


# --- explicit scope boundary: no ML, no probability, no optimization -------


def test_report_never_declares_an_ml_probability_or_optimization_field() -> None:
    from app.analytics.schema import StrategyAnalyticsReport, StrategyStat

    for model in (StrategyAnalyticsReport, StrategyStat):
        field_names = set(model.model_fields)
        for forbidden in ("probability", "optimi", "predict", "score", "confidence", "ranking"):
            assert not any(forbidden in name for name in field_names), (model, field_names)


async def test_report_explicitly_discloses_the_ml_limitation(client: AsyncClient) -> None:
    report = (await client.get("/analytics/strategy-report")).json()
    assert report["ml_model_status"] == "not_implemented"
    assert "no ml recovery-probability model" in report["ml_model_limitation"].lower()


# --- separation from Phase 5/6/7/8 -------------------------------------------


async def test_analytics_never_executes_an_action_or_changes_the_decision(
    client: AsyncClient,
) -> None:
    case_id, _failed_at = await _executed_case(
        client, external_reference="sa7", customer_external_id="cust-sa7"
    )
    before = (await client.get(f"/recovery/cases/{case_id}")).json()

    await client.get("/analytics/strategy-dataset")
    await client.get("/analytics/strategy-report")

    after = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert before == after
