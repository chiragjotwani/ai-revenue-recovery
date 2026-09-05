"""Phase 5H: the decision-policy evaluation harness must pass as part of
the normal test suite, not just when someone remembers to run
scripts/benchmark_decision_policy.py by hand.

This is a thin pytest wrapper around the harness's own functions -- it
does not duplicate the golden dataset or the policy rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from benchmark_decision_policy import (  # noqa: E402
    _determinism_stress,
    _load_cases,
    _run_cases,
    _safety_invariants,
)


def test_golden_decision_cases_all_pass() -> None:
    cases = _load_cases()
    results = _run_cases(cases)
    failures = [r for r in results if not r["passed"]]
    assert failures == [], failures


def test_decision_policy_safety_invariants_hold() -> None:
    cases = _load_cases()
    safety = _safety_invariants(cases)
    assert safety["passed"], safety["failures"]


def test_decision_policy_is_deterministic() -> None:
    cases = _load_cases()
    determinism = _determinism_stress(cases, repeats=10)
    assert determinism["passed"], determinism["failures"]


def test_golden_dataset_covers_every_documented_rule() -> None:
    """Every rule the policy docstring names should appear as at least one
    case's deciding rule -- a dataset gap would let a rule go unexercised.
    """
    cases = _load_cases()
    covered_rules = {case["expected"]["deciding_rule"] for case in cases}
    documented_rules = {
        "already_paid",
        "fraud_manual_review",
        "insufficient_evidence",
        "retry_cap",
        "retry_disposition_mismatch",
        "customer_action_compatibility",
        "default_pass_through",
        "default_safe_fallback",
    }
    assert documented_rules <= covered_rules, documented_rules - covered_rules
