"""Section 37 recovery-safety contracts (Phase 4.1, Workstream C3).

Section 37 of the engineering prompt mandates safety cases for: forbidden
action, duplicate action, already-recovered customer, and high-value
uncertain case. The decision engine (Phase 5) and action executor (Phase 6)
that would *enforce* these do not exist yet.

So this file has two halves:

* **Guarantees that hold today** -- assertions that the current system
  genuinely provides (the AI has no execution authority at all), which is
  the strongest form of "forbidden action is impossible".
* **Contracts for Phase 5/6** -- executable specifications marked
  ``xfail(strict=False)``. They describe exactly what the future layer
  MUST reject or route to manual review. When that layer lands they flip
  to ``xpass`` and the marker is removed. None of them execute a real
  financial action.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment

# --------------------------------------------------------------------------
# Guarantees that hold TODAY
# --------------------------------------------------------------------------


def _module_exists(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except ModuleNotFoundError:
        # a parent package is missing -> the module cannot exist
        return False


def test_no_action_executor_module_exists() -> None:
    """Forbidden action, strongest form: there is nothing that can execute a
    recovery action. If a future phase adds one, it must arrive together
    with the policy gate (ADR-003) and this test is updated deliberately.
    """
    for modname in (
        "app.recovery.executor",
        "app.recovery.actions",
        "app.actions",
        "app.decision.executor",
    ):
        assert not _module_exists(modname), f"{modname} exists without a policy gate"


async def test_diagnosis_never_mutates_a_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The AI path has no write access to payments (ADR-003)."""
    for i in range(3):
        await client.post(
            "/events",
            json={
                "idempotency_key": f"sc-s{i}",
                "event_type": "payment.succeeded",
                "source": "test",
                "occurred_at": datetime(2026, i + 1, 1, tzinfo=UTC).isoformat(),
                "customer": {"external_id": "sc-cust", "email": "sc@e.com"},
                "payment": {
                    "external_reference": f"sc-s{i}",
                    "amount": "4999.00",
                    "currency": "inr",
                },
            },
        )
    pid = (
        await client.post(
            "/events",
            json={
                "idempotency_key": "sc-f",
                "event_type": "payment.failed",
                "source": "test",
                "occurred_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
                "customer": {"external_id": "sc-cust", "email": "sc@e.com"},
                "payment": {
                    "external_reference": "sc-f",
                    "amount": "4999.00",
                    "currency": "inr",
                    "failure_reason": "insufficient_funds",
                },
            },
        )
    ).json()["payment_id"]

    before = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    snapshot = (before.status, before.amount, before.failure_reason)

    case_id = (await client.post("/recovery/cases", json={"payment_id": pid})).json()["id"]
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200

    db_session.expire_all()
    after = (await db_session.execute(select(Payment).where(Payment.id == pid))).scalar_one()
    assert (after.status, after.amount, after.failure_reason) == snapshot


async def test_a_payment_the_customer_later_paid_is_not_at_risk(client: AsyncClient) -> None:
    """Already-recovered customer, current guarantee: a failed payment with a
    later success for the same customer is excluded from the at-risk set, so
    nothing downstream would ever try to recover it.
    """
    base = datetime(2026, 6, 1, tzinfo=UTC)
    await client.post(
        "/events",
        json={
            "idempotency_key": "rc-f",
            "event_type": "payment.failed",
            "source": "test",
            "occurred_at": base.isoformat(),
            "customer": {"external_id": "rc-cust", "email": "rc@e.com"},
            "payment": {
                "external_reference": "rc-f",
                "amount": "4999.00",
                "currency": "inr",
                "failure_reason": "insufficient_funds",
            },
        },
    )
    await client.post(
        "/events",
        json={
            "idempotency_key": "rc-s",
            "event_type": "payment.succeeded",
            "source": "test",
            "occurred_at": (base + timedelta(hours=3)).isoformat(),
            "customer": {"external_id": "rc-cust", "email": "rc@e.com"},
            "payment": {"external_reference": "rc-s", "amount": "4999.00", "currency": "inr"},
        },
    )
    at_risk = (await client.get("/risk/payments")).json()
    assert at_risk == []


# --------------------------------------------------------------------------
# Contracts for Phase 5 / 6 (executable specs, not yet satisfiable)
# --------------------------------------------------------------------------

_PHASE5 = "Phase 5 decision engine / Phase 6 action executor not built yet"


@pytest.mark.xfail(reason=_PHASE5, strict=False, raises=(ImportError, ModuleNotFoundError))
def test_contract_forbidden_action_is_policy_rejected() -> None:
    """A diagnosis recommending an action the policy forbids (e.g. retrying
    past the retry cap, or a strategy not permitted for the disposition)
    MUST be rejected by the policy engine -- the action is never scheduled.
    """
    from app.decision.policy import evaluate  # noqa: F401

    raise AssertionError("write the real assertion when app.decision.policy exists")


@pytest.mark.xfail(reason=_PHASE5, strict=False, raises=(ImportError, ModuleNotFoundError))
def test_contract_duplicate_action_is_idempotent() -> None:
    """Scheduling the same action (same action-identity key) twice MUST
    return the existing action, never create or execute a second one.
    """
    from app.decision.actions import schedule_action  # noqa: F401

    raise AssertionError("write the real assertion when action scheduling exists")


@pytest.mark.xfail(reason=_PHASE5, strict=False, raises=(ImportError, ModuleNotFoundError))
def test_contract_already_recovered_customer_routes_to_no_action() -> None:
    """If the customer has already paid by another attempt, the decision
    engine MUST route the case to ``no_action`` / close it, never retry.
    """
    from app.decision.policy import evaluate  # noqa: F401

    raise AssertionError("write the real assertion when app.decision.policy exists")


@pytest.mark.xfail(reason=_PHASE5, strict=False, raises=(ImportError, ModuleNotFoundError))
def test_contract_high_value_low_confidence_escalates_to_manual_review() -> None:
    """A high-value payment diagnosed with low (model-reported) confidence
    MUST be escalated to manual review / human approval, never
    auto-actioned.
    """
    from app.decision.policy import evaluate  # noqa: F401

    raise AssertionError("write the real assertion when app.decision.policy exists")
