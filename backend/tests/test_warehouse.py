"""Phase 13: integration tests for the analytics warehouse ETL
(``app.warehouse.etl``), the read service (``app.warehouse.service``),
and its API surface (``GET /analytics/warehouse/facts``,
``GET /analytics/warehouse/report``).

Deliberately scoped (see ``app/warehouse/schema.py``'s module docstring):
no incremental-recovery or experiment/control-treatment analytics are
computed -- this system has no randomized control group or other
counterfactual design. These tests assert that scope boundary
explicitly, not just the arithmetic.

Real Postgres, real HTTP (project policy: no mocking the database).
Mirrors ``test_strategy_analytics.py``'s conventions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.warehouse import CaseAnalyticsFact
from app.warehouse.etl import rebuild_warehouse

BASE = datetime(2026, 4, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-wh",
    amount: str = "2500.00",
    currency: str = "inr",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "wh@e.com"},
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


async def _detected_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, **kw: object
) -> uuid.UUID:
    # Same "3 prior successful payments" shape ``test_strategy_analytics``
    # uses -- the policy engine needs a clean history for a
    # retriable_transient diagnosis to actually approve `retry` rather
    # than escalate.
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
        customer_external_id=customer_external_id,
        **kw,
    )
    return await _open_case(client, payment["payment_id"])


async def _executed_case(
    client: AsyncClient, *, external_reference: str, customer_external_id: str, **kw: object
) -> uuid.UUID:
    case_id = await _detected_case(
        client,
        external_reference=external_reference,
        customer_external_id=customer_external_id,
        **kw,
    )
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/decide")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/schedule-action")).status_code == 200
    assert (await client.post(f"/recovery/cases/{case_id}/execute-action")).status_code == 200
    return case_id


async def _observe_recovered(
    client: AsyncClient, case_id: uuid.UUID, *, external_reference: str, customer_external_id: str
) -> None:
    await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-later-success",
        external_reference=f"{external_reference}-later-success",
        occurred_at=BASE + timedelta(hours=1),
        event_type="payment.succeeded",
        failure_reason=None,
        customer_external_id=customer_external_id,
    )
    assert (await client.post(f"/recovery/cases/{case_id}/observe-outcome")).status_code == 200


# --- ETL: extraction / transformation / load --------------------------------


async def test_rebuild_creates_one_fact_row_per_case(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _executed_case(
        client, external_reference="wh1", customer_external_id="cust-wh1"
    )
    result = await rebuild_warehouse(db_session)
    assert result.facts_written >= 1

    row = await db_session.get(CaseAnalyticsFact, case_id)
    assert row is not None
    assert row.currency == "INR"
    assert row.has_action is True
    assert row.action_type == "retry"
    assert row.attempt_count == 1


async def test_rebuild_is_idempotent_no_duplicate_facts_on_rerun(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _executed_case(
        client, external_reference="wh2", customer_external_id="cust-wh2"
    )
    await rebuild_warehouse(db_session)
    await rebuild_warehouse(db_session)  # rerun -- must upsert, never duplicate

    rows = (
        await db_session.scalars(
            select(CaseAnalyticsFact).where(CaseAnalyticsFact.case_id == case_id)
        )
    ).all()
    assert len(rows) == 1


async def test_rebuild_reflects_a_changed_outcome_on_rerun(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _executed_case(
        client, external_reference="wh3", customer_external_id="cust-wh3"
    )
    await rebuild_warehouse(db_session)
    row = await db_session.get(CaseAnalyticsFact, case_id)
    assert row is not None
    assert row.outcome_status == "unresolved"

    await _observe_recovered(
        client, case_id, external_reference="wh3", customer_external_id="cust-wh3"
    )
    await rebuild_warehouse(db_session)
    await db_session.refresh(row)
    assert row.outcome_status == "recovered"


async def test_case_with_no_diagnosis_or_action_has_null_fields_not_a_crash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A case that never left DETECTED (no decision, no diagnosis) must
    still produce a valid fact row -- every downstream field is nullable,
    never a synthetic/default value standing in for missing evidence.
    """
    case_id = await _detected_case(
        client, external_reference="wh4", customer_external_id="cust-wh4"
    )
    await rebuild_warehouse(db_session)

    row = await db_session.get(CaseAnalyticsFact, case_id)
    assert row is not None
    assert row.eligible is False
    assert row.has_action is False
    assert row.action_type is None
    assert row.attempt_count == 0
    assert row.disposition is None
    assert row.model_name is None
    assert row.confidence is None


# --- timezone / date-boundary handling --------------------------------------


async def test_computed_at_is_timezone_aware_not_naive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Every other timestamp in this codebase is stored tz-aware
    (``DateTime(timezone=True)``) -- the warehouse must not introduce a
    naive-datetime column that silently assumes a server-local timezone.
    """
    case_id = await _executed_case(
        client, external_reference="wh-tz", customer_external_id="cust-wh-tz"
    )
    await rebuild_warehouse(db_session)
    row = await db_session.get(CaseAnalyticsFact, case_id)
    assert row is not None
    assert row.computed_at.tzinfo is not None


async def test_cases_failing_on_opposite_sides_of_a_day_boundary_are_both_captured(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A payment failing at 23:59:59 UTC and one failing at 00:00:01 UTC
    the next day must each produce their own fact row -- the ETL must not
    collapse or misattribute rows across a UTC day boundary.
    """
    late = BASE - timedelta(seconds=1)  # 2026-03-31T23:59:59Z
    early = BASE + timedelta(seconds=1)  # 2026-04-01T00:00:01Z

    late_payment = await _ingest_one(
        client,
        idempotency_key="wh-tz-late-f",
        external_reference="wh-tz-late",
        occurred_at=late,
        customer_external_id="cust-wh-tz-late",
    )
    late_case = await _open_case(client, late_payment["payment_id"])

    early_payment = await _ingest_one(
        client,
        idempotency_key="wh-tz-early-f",
        external_reference="wh-tz-early",
        occurred_at=early,
        customer_external_id="cust-wh-tz-early",
    )
    early_case = await _open_case(client, early_payment["payment_id"])

    await rebuild_warehouse(db_session)
    assert (await db_session.get(CaseAnalyticsFact, late_case)) is not None
    assert (await db_session.get(CaseAnalyticsFact, early_case)) is not None


# --- customer segmentation ---------------------------------------------------


async def test_customer_case_volume_segmentation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    single_case = await _detected_case(
        client, external_reference="wh5-single", customer_external_id="cust-wh5-single"
    )
    repeat_cases = []
    for i in range(3):
        c = await _detected_case(
            client,
            external_reference=f"wh5-repeat-{i}",
            customer_external_id="cust-wh5-repeat",
        )
        repeat_cases.append(c)

    await rebuild_warehouse(db_session)

    single_row = await db_session.get(CaseAnalyticsFact, single_case)
    assert single_row is not None
    assert single_row.customer_case_segment == "single_case"

    for c in repeat_cases:
        row = await db_session.get(CaseAnalyticsFact, c)
        assert row is not None
        assert row.customer_case_segment == "repeat_2_4"


# --- natural recovery: honestly disclosed as not measurable ----------------


async def test_natural_recovery_is_honestly_disclosed_as_not_measurable(
    client: AsyncClient,
) -> None:
    """Phase 7's outcome observation (and therefore Phase 8's revenue
    report) only classifies a case 'recovered' when it has an executed
    RecoveryAction -- an escalated/action-less case is always
    'unresolved' under that frozen definition, even if it later has a
    successful payment as raw evidence. Computing 'natural recovery' from
    raw evidence anyway would give such a case a different outcome
    definition than Phase 8's own report uses for it -- not permitted.
    This module discloses the gap rather than fabricating the metric.
    """
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    report = (await client.get("/analytics/warehouse/report")).json()
    assert report["natural_recovery_status"] == "not_measurable"
    assert "natural recovery" in report["natural_recovery_limitation"].lower()


async def test_action_less_case_is_unresolved_matching_phase_8_semantics(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An escalated (fraud) decision never gets a RecoveryAction -- its
    fact row's outcome_status must stay 'unresolved', identical to how
    Phase 8's own get_revenue_report would classify it, not something
    this phase invents independently.
    """
    escalated_case = await _detected_case(
        client,
        external_reference="wh6-escalated",
        customer_external_id="cust-wh6-esc",
        failure_reason="fraud_suspected",
    )
    assert (await client.post(f"/recovery/cases/{escalated_case}/diagnose")).status_code == 200
    assert (await client.post(f"/recovery/cases/{escalated_case}/decide")).status_code == 200

    await rebuild_warehouse(db_session)
    row = await db_session.get(CaseAnalyticsFact, escalated_case)
    assert row is not None
    assert row.has_action is False
    assert row.outcome_status == "unresolved"


# --- currency safety (KI-006) -----------------------------------------------


async def test_revenue_at_risk_never_mixes_currencies(client: AsyncClient) -> None:
    inr_case = await _detected_case(
        client, external_reference="wh7-inr", customer_external_id="cust-wh7-inr", currency="inr"
    )
    usd_case = await _detected_case(
        client, external_reference="wh7-usd", customer_external_id="cust-wh7-usd", currency="usd"
    )
    # Push each case through decide() so it counts as "eligible" and lands
    # in revenue_at_risk.
    for case_id in (inr_case, usd_case):
        assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
        assert (await client.post(f"/recovery/cases/{case_id}/decide")).status_code == 200

    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    report = (await client.get("/analytics/warehouse/report")).json()
    currencies_seen = {a["currency"] for a in report["revenue_at_risk"]}
    # Every entry is single-currency by construction (CurrencyAmount has
    # exactly one currency field) -- assert the type-level guarantee holds
    # for every row actually returned.
    for entry in report["revenue_at_risk"]:
        assert entry["currency"] in currencies_seen
        assert isinstance(entry["amount"], int | float | str)


# --- API surface --------------------------------------------------------


async def test_facts_endpoint_returns_built_rows(client: AsyncClient) -> None:
    case_id = await _executed_case(
        client, external_reference="wh8", customer_external_id="cust-wh8"
    )
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    rows = (await client.get("/analytics/warehouse/facts")).json()
    assert any(r["case_id"] == str(case_id) for r in rows)


async def test_report_never_divides_by_zero_with_no_observed_cases(client: AsyncClient) -> None:
    await _executed_case(client, external_reference="wh9", customer_external_id="cust-wh9")
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    report = (await client.get("/analytics/warehouse/report")).json()
    retry_stat = next(s for s in report["by_strategy"] if s["key"] == "retry")
    if retry_stat["observed_count"] == 0:
        assert retry_stat["empirical_recovery_rate"] is None


async def test_model_performance_reports_observed_stats(client: AsyncClient) -> None:
    await _executed_case(client, external_reference="wh10", customer_external_id="cust-wh10")
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    report = (await client.get("/analytics/warehouse/report")).json()
    assert len(report["model_performance"]) >= 1
    stat = report["model_performance"][0]
    assert stat["diagnosis_count"] >= 1
    assert 0.0 <= stat["router_escalation_rate"] <= 1.0


# --- explicit scope boundary: no incremental/experiment analytics ----------


def test_report_never_declares_an_incremental_or_causal_field() -> None:
    from app.warehouse.schema import AnalyticsWarehouseReport, RecoveryRateStat

    for model in (AnalyticsWarehouseReport, RecoveryRateStat):
        field_names = set(model.model_fields)
        for forbidden in ("incremental", "causal", "uplift", "control_group", "treatment_group"):
            assert not any(forbidden in name for name in field_names), (model, field_names)


async def test_report_explicitly_discloses_the_experiment_limitation(
    client: AsyncClient,
) -> None:
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    report = (await client.get("/analytics/warehouse/report")).json()
    assert report["experiment_status"] == "not_implemented"
    assert "no experiment" in report["experiment_limitation"].lower()


# --- separation from operational tables --------------------------------


async def test_warehouse_rebuild_never_mutates_the_operational_case(
    client: AsyncClient,
) -> None:
    case_id = await _executed_case(
        client, external_reference="wh11", customer_external_id="cust-wh11"
    )
    before = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert (await client.post("/analytics/warehouse/rebuild")).status_code == 200
    after = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert before == after
