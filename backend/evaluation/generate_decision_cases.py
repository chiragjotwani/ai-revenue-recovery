"""Generate the Phase 5 decision-policy evaluation dataset.

Deterministic: run with no arguments to (re)write ``decision_cases.json``
next to this file. The JSON file -- not this script -- is the artifact of
record and must not be edited to make the policy look better (same
discipline as KI-007 / ``generate_diagnosis_cases.py``).

Every case here is a hand-constructed ``PolicyInput`` plus the expected
``PolicyOutcome`` fields -- there is no model, no synthetic label
generator, and no claim of real-world validity. This dataset tests one
thing only: does ``app.decision.policy.evaluate`` implement its own
documented rules correctly, deterministically, and safely. It says
nothing about real recovered revenue (KI-007's synthetic-data caveat
applies with equal force here; see docs/known-issues.md).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_PATH = Path(__file__).with_name("decision_cases.json")

_NOW = "2026-03-01T00:00:00+00:00"


def _case(
    case_id: str,
    category: str,
    *,
    disposition: str,
    candidate_strategy: str,
    recommended_delay_hours: int | None,
    evidence_sufficiency: str,
    signals_conflict: bool,
    retry_count: int,
    already_paid: bool,
    expected_approved_strategy: str,
    expected_decision_status: str,
    expected_recoverability: str,
    expected_deciding_rule: str,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": category,
        "input": {
            "disposition": disposition,
            "candidate_strategy": candidate_strategy,
            "recommended_delay_hours": recommended_delay_hours,
            "evidence_sufficiency": evidence_sufficiency,
            "signals_conflict": signals_conflict,
            "retry_count": retry_count,
            "already_paid": already_paid,
            "now": _NOW,
        },
        "expected": {
            "approved_strategy": expected_approved_strategy,
            "decision_status": expected_decision_status,
            "recoverability": expected_recoverability,
            # The rule_id of the LAST rationale entry -- evaluate() always
            # appends exactly one entry immediately before returning, so
            # this identifies which rule actually determined the outcome
            # (whether its own recorded outcome was "failed", "passed", or
            # "not_applicable").
            "deciding_rule": expected_deciding_rule,
        },
    }


CASES: list[dict[str, object]] = [
    _case(
        "01_retriable_transient",
        "retriable transient failure",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="retry",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
    _case(
        "02_retry_cap_reached",
        "retry cap reached",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=3,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
    _case(
        "03_insufficient_evidence",
        "insufficient evidence",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sparse",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="insufficient_evidence",
        expected_deciding_rule="insufficient_evidence",
    ),
    _case(
        "04_conflicting_signals",
        "conflicting signals",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=True,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="insufficient_evidence",
        expected_deciding_rule="insufficient_evidence",
    ),
    _case(
        "05_customer_action_compatible",
        "customer action required + compatible strategy",
        disposition="customer_action_required",
        candidate_strategy="request_payment_method_update",
        recommended_delay_hours=24,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="request_payment_method_update",
        expected_decision_status="approved",
        expected_recoverability="conditionally_recoverable",
        expected_deciding_rule="customer_action_compatibility",
    ),
    _case(
        "06_customer_action_incompatible",
        "customer action required + incompatible strategy",
        disposition="customer_action_required",
        candidate_strategy="manual_review",
        recommended_delay_hours=None,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="conditionally_recoverable",
        expected_deciding_rule="customer_action_compatibility",
    ),
    _case(
        "07_suspected_fraud",
        "suspected fraud",
        disposition="suspected_fraud",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="not_recoverable_automatically",
        expected_deciding_rule="fraud_manual_review",
    ),
    _case(
        "08_already_paid",
        "already paid",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=True,
        expected_approved_strategy="no_action",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="already_paid",
    ),
    _case(
        "09_manual_review_default",
        "manual review (default pass-through)",
        disposition="unknown",
        candidate_strategy="manual_review",
        recommended_delay_hours=None,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="insufficient_evidence",
        expected_deciding_rule="default_pass_through",
    ),
    _case(
        "10_no_action_default",
        "no-action (default pass-through)",
        disposition="unknown",
        candidate_strategy="no_action",
        recommended_delay_hours=None,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="no_action",
        expected_decision_status="approved",
        expected_recoverability="insufficient_evidence",
        expected_deciding_rule="default_pass_through",
    ),
    _case(
        "11a_invalid_retry_disposition_mismatch",
        "invalid combination: retry proposed for a non-retriable disposition",
        disposition="customer_action_required",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="conditionally_recoverable",
        expected_deciding_rule="retry_disposition_mismatch",
    ),
    _case(
        "11b_invalid_unhandled_combination",
        "invalid combination: no rule admits this candidate strategy",
        disposition="retriable_transient",
        candidate_strategy="contact_customer",
        recommended_delay_hours=12,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="manual_review",
        expected_decision_status="escalated",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="default_safe_fallback",
    ),
    _case(
        "12a_boundary_delay_zero",
        "boundary timing: recommended_delay_hours = 0 (minimum)",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=0,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="retry",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
    _case(
        "12b_boundary_delay_max",
        "boundary timing: recommended_delay_hours = 720 (maximum)",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=720,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="retry",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
    _case(
        "12c_boundary_retry_count_just_under_cap",
        "boundary timing: retry_count = RETRY_CAP - 1 (still approved)",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=6,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=2,
        already_paid=False,
        expected_approved_strategy="retry",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
    _case(
        "12d_boundary_no_delay",
        "boundary timing: recommended_delay_hours = None (no schedule)",
        disposition="retriable_transient",
        candidate_strategy="retry",
        recommended_delay_hours=None,
        evidence_sufficiency="sufficient",
        signals_conflict=False,
        retry_count=0,
        already_paid=False,
        expected_approved_strategy="retry",
        expected_decision_status="approved",
        expected_recoverability="likely_recoverable",
        expected_deciding_rule="retry_cap",
    ),
]


def main() -> None:
    OUT_PATH.write_text(json.dumps(CASES, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} cases to {OUT_PATH}")


if __name__ == "__main__":
    main()
