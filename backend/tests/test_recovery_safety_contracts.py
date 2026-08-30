"""Section 37 recovery-safety contracts (Phase 4.1, Workstream C3; policy
contracts filled in by Phase 5B).

Section 37 of the engineering prompt mandates safety cases for: forbidden
action, duplicate action, already-recovered customer, insufficient/
conflicting evidence, and high-value uncertain case. The decision engine
(Phase 5B, ``app.decision.policy``) now exists and enforces the first four;
the action executor (Phase 6) that would enforce true duplicate-action
idempotency does not exist yet.

So this file has three parts:

* **Guarantees that hold today independent of Phase 5/6** -- assertions
  the current system genuinely provides (the AI has no execution authority
  at all), the strongest form of "forbidden action is impossible".
* **Phase 5B contracts, now real assertions** -- forbidden action, already-
  recovered customer, and insufficient/conflicting evidence all exercise
  ``app.decision.policy.evaluate`` directly and pass.
* **Still-deferred contracts** -- duplicate-action idempotency (needs
  Phase 6's ``app.decision.actions``) and high-value escalation (owner-
  decision-pending; see ADR-006 and KI-006) remain ``xfail``, explicitly
  documented as blocked, not silently dropped.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.schema import DecisionStatus
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
# Phase 5B policy contracts (real assertions -- app.decision.policy exists)
# --------------------------------------------------------------------------

_PHASE5 = "Phase 5 decision engine / Phase 6 action executor not built yet"
_PHASE5_HIGH_VALUE = (
    "deferred: no canonical high-value definition, currency basis, or "
    "threshold exists yet (KI-006, ADR-006) -- owner decision pending"
)


def test_contract_forbidden_action_is_policy_rejected() -> None:
    """A diagnosis recommending an action the policy forbids (e.g. retrying
    past the retry cap, or a strategy not permitted for the disposition)
    MUST be rejected by the policy engine -- the action is never scheduled.

    Implemented by Phase 5B (app.decision.policy). "Rejected by the policy
    engine" here means the forbidden action (retry) is never the approved
    strategy -- it is downgraded to manual_review, never executed.
    """
    from datetime import UTC, datetime

    from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
    from app.decision.policy import RETRY_CAP, PolicyInput, evaluate

    # Retrying past the retry cap is a forbidden action.
    past_cap = evaluate(
        PolicyInput(
            disposition=DiagnosisDisposition.RETRIABLE_TRANSIENT,
            candidate_strategy=RecoveryStrategy.RETRY,
            recommended_delay_hours=6,
            evidence_sufficiency="sufficient",
            signals_conflict=False,
            retry_count=RETRY_CAP,
            already_paid=False,
            now=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    assert past_cap.approved_strategy is not RecoveryStrategy.RETRY

    # A retry strategy is not permitted for a suspected-fraud disposition.
    fraud = evaluate(
        PolicyInput(
            disposition=DiagnosisDisposition.SUSPECTED_FRAUD,
            candidate_strategy=RecoveryStrategy.RETRY,
            recommended_delay_hours=6,
            evidence_sufficiency="sufficient",
            signals_conflict=False,
            retry_count=0,
            already_paid=False,
            now=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    assert fraud.approved_strategy is not RecoveryStrategy.RETRY


@pytest.mark.xfail(reason=_PHASE5, strict=False, raises=(ImportError, ModuleNotFoundError))
def test_contract_duplicate_action_is_idempotent() -> None:
    """Scheduling the same action (same action-identity key) twice MUST
    return the existing action, never create or execute a second one.
    """
    from app.decision.actions import schedule_action  # noqa: F401

    raise AssertionError("write the real assertion when action scheduling exists")


def test_contract_already_recovered_customer_routes_to_no_action() -> None:
    """If the customer has already paid by another attempt, the decision
    engine MUST route the case to ``no_action`` / close it, never retry.

    Implemented by Phase 5B (app.decision.policy).
    """
    from datetime import UTC, datetime

    from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
    from app.decision.policy import PolicyInput, evaluate

    outcome = evaluate(
        PolicyInput(
            disposition=DiagnosisDisposition.RETRIABLE_TRANSIENT,
            candidate_strategy=RecoveryStrategy.RETRY,
            recommended_delay_hours=6,
            evidence_sufficiency="sufficient",
            signals_conflict=False,
            retry_count=0,
            already_paid=True,
            now=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    assert outcome.approved_strategy is RecoveryStrategy.NO_ACTION
    assert outcome.approved_strategy is not RecoveryStrategy.RETRY


def test_contract_insufficient_or_conflicting_evidence_escalates_to_manual_review() -> None:
    """Insufficient or conflicting evidence MUST be escalated to manual
    review / human approval, never auto-actioned.

    This is the project's deterministic proxy for "the model wasn't
    confident enough to trust" (see ADR-006): Phase 4's own confidence
    field is model-reported and explicitly documented as uncalibrated
    (``app/ai/schema.py::ModelDiagnosisJSON.confidence``), so this contract
    is enforced through the two typed, database-derived signals the
    context builder already computes -- ``evidence_sufficiency`` and
    ``signals_conflict`` -- never through a numeric cutoff on that
    self-reported number. Implemented by Phase 5B (app.decision.policy).
    """
    from datetime import UTC, datetime

    from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
    from app.decision.policy import PolicyInput, evaluate

    sparse = evaluate(
        PolicyInput(
            disposition=DiagnosisDisposition.RETRIABLE_TRANSIENT,
            candidate_strategy=RecoveryStrategy.RETRY,
            recommended_delay_hours=6,
            evidence_sufficiency="sparse",
            signals_conflict=False,
            retry_count=0,
            already_paid=False,
            now=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    assert sparse.approved_strategy is RecoveryStrategy.MANUAL_REVIEW
    assert sparse.decision_status is DecisionStatus.ESCALATED

    conflicting = evaluate(
        PolicyInput(
            disposition=DiagnosisDisposition.RETRIABLE_TRANSIENT,
            candidate_strategy=RecoveryStrategy.RETRY,
            recommended_delay_hours=6,
            evidence_sufficiency="sufficient",
            signals_conflict=True,
            retry_count=0,
            already_paid=False,
            now=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    assert conflicting.approved_strategy is RecoveryStrategy.MANUAL_REVIEW
    assert conflicting.decision_status is DecisionStatus.ESCALATED


@pytest.mark.xfail(reason=_PHASE5_HIGH_VALUE, strict=False, raises=AssertionError)
def test_contract_high_value_escalates_to_manual_review() -> None:
    """DEFERRED -- owner-decision-pending, not implemented in Phase 5.

    A high-value payment MUST eventually be escalated to manual review
    regardless of diagnosis, but this contract cannot be implemented yet:
    the repository has no canonical definition of "high value" (no amount
    threshold exists anywhere in ``backend/app``), no currency basis or
    normalization strategy (KI-006, `docs/known-issues.md`, remains
    unresolved for cross-currency amounts), and no owner-approved threshold
    value. Implementing any of these would mean fabricating a policy
    input this project has explicitly decided not to invent (see
    ADR-006). This test stays ``xfail`` -- not deleted, not silently
    dropped -- until an owner decision supplies all three missing pieces.
    """
    from app.decision.policy import evaluate  # noqa: F401

    raise AssertionError(
        "blocked: no canonical high-value definition, currency basis, or threshold exists (KI-006)"
    )
