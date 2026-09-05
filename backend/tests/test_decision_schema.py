"""Phase 5A: Decision domain contract tests.

Pure: constructs app.decision.schema objects directly, no database, no
policy logic (5B), no API (5F). Mirrors the style of test_ai_diagnosis.py's
schema-validation section.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ai.schema import RecoveryStrategy
from app.decision.schema import (
    DECISION_ENGINE_VERSION,
    DecisionIdentity,
    DecisionRationaleEntry,
    DecisionResult,
    DecisionStatus,
    Recoverability,
)

_CASE_ID = uuid.uuid4()
_DIAGNOSIS_ID = uuid.uuid4()


def _identity() -> DecisionIdentity:
    return DecisionIdentity(case_id=_CASE_ID, diagnosis_id=_DIAGNOSIS_ID)


def _decision(**overrides: object) -> DecisionResult:
    base: dict[str, object] = {
        "identity": _identity(),
        "recoverability": Recoverability.LIKELY_RECOVERABLE,
        "candidate_strategy": RecoveryStrategy.RETRY,
        "approved_strategy": RecoveryStrategy.RETRY,
        "decision_status": DecisionStatus.APPROVED,
        "rationale": [],
        "scheduled_not_before": None,
    }
    base.update(overrides)
    return DecisionResult.model_validate(base)


# --- Recoverability ---------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "likely_recoverable",
        "conditionally_recoverable",
        "not_recoverable_automatically",
        "insufficient_evidence",
    ],
)
def test_recoverability_accepts_the_approved_taxonomy(value: str) -> None:
    assert Recoverability(value) is not None


def test_recoverability_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        Recoverability("very_likely_recoverable")


def test_recoverability_has_exactly_four_members() -> None:
    # Pins the approved taxonomy size: no confidence-threshold-derived
    # fifth class should be added without a deliberate architecture change.
    assert len(list(Recoverability)) == 4


# --- Decision status ----------------------------------------------------


@pytest.mark.parametrize("value", ["approved", "rejected", "escalated", "superseded"])
def test_decision_status_accepts_the_approved_semantics(value: str) -> None:
    assert DecisionStatus(value) is not None


def test_decision_status_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        DecisionStatus("pending_review")


# --- Strategy reuse (no second vocabulary) -------------------------------


def test_decision_result_reuses_the_existing_recovery_strategy_enum() -> None:
    decision = _decision(
        candidate_strategy=RecoveryStrategy.CONTACT_CUSTOMER,
        approved_strategy=RecoveryStrategy.CONTACT_CUSTOMER,
    )
    assert decision.candidate_strategy is RecoveryStrategy.CONTACT_CUSTOMER
    assert isinstance(decision.approved_strategy, RecoveryStrategy)


def test_candidate_and_approved_strategy_may_differ() -> None:
    # A downgrade: AI proposed retry, policy approved manual_review instead.
    decision = _decision(
        candidate_strategy=RecoveryStrategy.RETRY,
        approved_strategy=RecoveryStrategy.MANUAL_REVIEW,
        decision_status=DecisionStatus.APPROVED,
    )
    assert decision.candidate_strategy is RecoveryStrategy.RETRY
    assert decision.approved_strategy is RecoveryStrategy.MANUAL_REVIEW


def test_rejects_an_invalid_strategy_string() -> None:
    with pytest.raises(ValidationError):
        _decision(candidate_strategy="delete_customer")


# --- Rationale ------------------------------------------------------------


def test_rationale_entry_represents_a_typed_rule_outcome() -> None:
    entry = DecisionRationaleEntry(
        rule_id="retry_cap", outcome="failed", reason_code="cap_exceeded"
    )
    assert entry.rule_id == "retry_cap"
    assert entry.outcome == "failed"
    assert entry.reason_code == "cap_exceeded"


def test_rationale_entry_reason_code_is_optional() -> None:
    entry = DecisionRationaleEntry(rule_id="already_paid", outcome="not_applicable")
    assert entry.reason_code is None


def test_rationale_entry_rejects_an_unknown_outcome_value() -> None:
    with pytest.raises(ValidationError):
        DecisionRationaleEntry(rule_id="retry_cap", outcome="maybe")


def test_rationale_entry_has_no_free_text_reasoning_field() -> None:
    # Guards the AI trust boundary: rationale must never carry a free-text
    # field a future implementation could be tempted to fill from
    # diagnosis.reasoning.
    assert "reasoning" not in DecisionRationaleEntry.model_fields


def test_decision_result_carries_a_list_of_rationale_entries() -> None:
    decision = _decision(
        rationale=[
            DecisionRationaleEntry(rule_id="already_paid", outcome="not_applicable"),
            DecisionRationaleEntry(rule_id="retry_cap", outcome="passed"),
        ]
    )
    assert [r.rule_id for r in decision.rationale] == ["already_paid", "retry_cap"]


# --- Provenance -------------------------------------------------------


def test_decision_result_carries_a_decision_engine_version() -> None:
    decision = _decision()
    assert decision.decision_engine_version == DECISION_ENGINE_VERSION


def test_decision_result_has_exactly_one_version_field() -> None:
    # Architecture Revision Issue 7: one version field, not a separate
    # policy_version + engine_version pair.
    version_fields = [name for name in DecisionResult.model_fields if "version" in name]
    assert version_fields == ["decision_engine_version"]


# --- Decision identity ----------------------------------------------------


def test_decision_identity_is_case_and_diagnosis_id() -> None:
    identity = _identity()
    assert identity.case_id == _CASE_ID
    assert identity.diagnosis_id == _DIAGNOSIS_ID


def test_decision_identity_has_no_extra_fields() -> None:
    # Guards against a preliminary existence-check field (e.g. a bolted-on
    # "already_exists" flag) sneaking into the identity type -- identity is
    # only ever (case_id, diagnosis_id); DB uniqueness is a later workstream.
    assert set(DecisionIdentity.model_fields) == {"case_id", "diagnosis_id"}


# --- Timing -----------------------------------------------------------


def test_scheduled_not_before_accepts_a_timestamp() -> None:
    when = datetime(2026, 9, 1, tzinfo=UTC)
    decision = _decision(scheduled_not_before=when)
    assert decision.scheduled_not_before == when


def test_scheduled_not_before_is_none_for_strategies_with_no_timing_meaning() -> None:
    decision = _decision(
        candidate_strategy=RecoveryStrategy.MANUAL_REVIEW,
        approved_strategy=RecoveryStrategy.MANUAL_REVIEW,
        decision_status=DecisionStatus.ESCALATED,
        scheduled_not_before=None,
    )
    assert decision.scheduled_not_before is None


# --- Already-paid / no_action -------------------------------------------


def test_already_paid_is_represented_as_approved_no_action_not_a_new_status() -> None:
    # Architecture Revision: "already paid" is a policy condition, not a
    # new lifecycle/decision status.
    decision = _decision(
        candidate_strategy=RecoveryStrategy.RETRY,
        approved_strategy=RecoveryStrategy.NO_ACTION,
        decision_status=DecisionStatus.APPROVED,
        rationale=[DecisionRationaleEntry(rule_id="already_paid", outcome="failed")],
    )
    assert decision.approved_strategy is RecoveryStrategy.NO_ACTION
    assert decision.decision_status is DecisionStatus.APPROVED
    assert "already_paid" not in {s.value for s in DecisionStatus}


# --- No monetary threshold field ------------------------------------------


def test_decision_result_has_no_amount_or_currency_field() -> None:
    # KI-006 remains unresolved for cross-currency amounts; Phase 5
    # deliberately introduces no amount-based field anywhere.
    forbidden = {"amount", "currency", "high_value", "value_threshold"}
    assert forbidden.isdisjoint(DecisionResult.model_fields)


def test_decision_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _decision(amount="1000.00")
