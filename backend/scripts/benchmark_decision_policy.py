"""Evaluate the Phase 5 deterministic decision policy engine.

Usage:
    python scripts/benchmark_decision_policy.py
    python scripts/benchmark_decision_policy.py --json out.json

Measures, over ``evaluation/decision_cases.json``:
  - per-case correctness (approved_strategy / decision_status /
    recoverability / deciding rule match the golden expectation)
  - safety invariants (fraud never retries, already-paid never becomes
    an active recovery strategy, sparse/conflicting evidence never
    auto-recovers, retry cap is respected, no monetary or confidence
    threshold exists anywhere in the input/output schema, no free-text
    reasoning field exists on PolicyInput, unhandled combinations
    degrade to escalated/manual_review rather than being silently
    approved)
  - determinism (every case evaluated twice; results must be identical)

--------------------------------------------------------------------------
UNIT / GOLDEN POLICY CASES vs. SYNTHETIC EVALUATION DATA vs. REAL DATA
--------------------------------------------------------------------------
This harness runs ONLY the first category: a small, fixed, hand-authored
set of golden cases that exercises ``app.decision.policy.evaluate``'s own
documented rule branches. It is not a synthetic-dataset benchmark in the
sense ``benchmark_diagnosis.py`` is (that script scores a model against
many generated cases sharing a label-generation function with the mock
provider -- see KI-007); there is no generator/label-sharing concern here
because ``evaluate`` is a pure, fully-specified function being checked
against its own specification, not a model being scored against a proxy
for ground truth.

This harness NEVER measures, claims, or implies:
  - real-world recovered revenue
  - production conversion rates
  - before/after business impact
  - real customer payment outcomes
Those require live recovery-outcome data (Phase 8+) and are explicitly
out of scope for Phase 5 (KI-007 remains relevant: no evaluation in this
codebase substitutes for real-world validation).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
from app.decision.policy import RETRY_CAP, PolicyInput, evaluate
from app.decision.schema import DecisionStatus

_DATASET = Path(__file__).resolve().parent.parent / "evaluation" / "decision_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(_DATASET.read_text(encoding="utf-8"))
    return cases


def _to_policy_input(raw: dict[str, Any]) -> PolicyInput:
    return PolicyInput(
        disposition=DiagnosisDisposition(raw["disposition"]),
        candidate_strategy=RecoveryStrategy(raw["candidate_strategy"]),
        recommended_delay_hours=raw["recommended_delay_hours"],
        evidence_sufficiency=raw["evidence_sufficiency"],
        signals_conflict=raw["signals_conflict"],
        retry_count=raw["retry_count"],
        already_paid=raw["already_paid"],
        now=datetime.fromisoformat(raw["now"]),
    )


def _deciding_rule(rationale: list[Any]) -> str:
    """The rule that actually determined this outcome: `evaluate` appends
    exactly one rationale entry immediately before every `return`, so the
    last entry is always the deciding one -- regardless of whether its
    own outcome was "failed" (the rule blocked something) or "passed"
    (e.g. the two default-pass-through rules, which decide the outcome by
    passing, not failing).
    """
    return rationale[-1].rule_id if rationale else "none"


def _run_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        policy_input = _to_policy_input(case["input"])
        outcome = evaluate(policy_input)
        # Determinism: evaluate the identical input a second time.
        outcome_repeat = evaluate(policy_input)
        deterministic = outcome.model_dump() == outcome_repeat.model_dump()

        expected = case["expected"]
        actual_deciding_rule = _deciding_rule(outcome.rationale)
        checks = {
            "approved_strategy": outcome.approved_strategy.value == expected["approved_strategy"],
            "decision_status": outcome.decision_status.value == expected["decision_status"],
            "recoverability": outcome.recoverability.value == expected["recoverability"],
            "deciding_rule": actual_deciding_rule == expected["deciding_rule"],
        }
        passed = all(checks.values()) and deterministic

        results.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "passed": passed,
                "deterministic": deterministic,
                "checks": checks,
                "actual": {
                    "approved_strategy": outcome.approved_strategy.value,
                    "decision_status": outcome.decision_status.value,
                    "recoverability": outcome.recoverability.value,
                    "deciding_rule": actual_deciding_rule,
                    "scheduled_not_before": (
                        outcome.scheduled_not_before.isoformat()
                        if outcome.scheduled_not_before
                        else None
                    ),
                },
                "expected": expected,
            }
        )
    return results


def _safety_invariants(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-case invariants a correctness check on individual cases
    cannot express by itself.
    """
    failures: list[str] = []

    for case in cases:
        inp = case["input"]
        policy_input = _to_policy_input(inp)
        outcome = evaluate(policy_input)

        if inp["disposition"] == "suspected_fraud" and outcome.approved_strategy is (
            RecoveryStrategy.RETRY
        ):
            failures.append(f"{case['case_id']}: fraud case was approved to retry")

        if inp["already_paid"] and outcome.approved_strategy is not RecoveryStrategy.NO_ACTION:
            failures.append(f"{case['case_id']}: already-paid did not route to no_action")

        if (
            inp["evidence_sufficiency"] == "sparse"
            and outcome.decision_status is DecisionStatus.APPROVED
            and outcome.approved_strategy is RecoveryStrategy.RETRY
        ):
            failures.append(f"{case['case_id']}: sparse evidence auto-recovered via retry")

        if (
            inp["signals_conflict"]
            and outcome.decision_status is DecisionStatus.APPROVED
            and outcome.approved_strategy is RecoveryStrategy.RETRY
        ):
            failures.append(f"{case['case_id']}: conflicting evidence auto-recovered via retry")

        if (
            inp["candidate_strategy"] == "retry"
            and inp["disposition"] == "retriable_transient"
            and inp["retry_count"] >= RETRY_CAP
            and outcome.approved_strategy is RecoveryStrategy.RETRY
        ):
            failures.append(f"{case['case_id']}: retry cap was not respected")

    # Structural invariants (schema-level, checked once, not per-case):
    # no monetary/confidence field, no free-text reasoning field.
    policy_input_fields = set(PolicyInput.model_fields)
    forbidden_fields = {"amount", "currency", "confidence", "reasoning", "high_value"}
    leaked = policy_input_fields & forbidden_fields
    if leaked:
        failures.append(f"PolicyInput carries forbidden field(s): {sorted(leaked)}")
    if PolicyInput.model_config.get("extra") != "forbid":
        failures.append("PolicyInput does not forbid extra fields (a caller could smuggle one in)")

    return {"failures": failures, "passed": len(failures) == 0}


def _determinism_stress(cases: list[dict[str, Any]], repeats: int = 20) -> dict[str, Any]:
    """Re-evaluate every case `repeats` times and confirm every run is
    byte-identical to the first -- a stronger check than the 2x check
    embedded in `_run_cases`.
    """
    failures: list[str] = []
    for case in cases:
        policy_input = _to_policy_input(case["input"])
        first = evaluate(policy_input).model_dump()
        for _ in range(repeats - 1):
            if evaluate(policy_input).model_dump() != first:
                failures.append(f"{case['case_id']}: non-deterministic across {repeats} runs")
                break
    return {"failures": failures, "passed": len(failures) == 0, "repeats_per_case": repeats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the full report as JSON")
    args = parser.parse_args()

    cases = _load_cases()
    results = _run_cases(cases)
    safety = _safety_invariants(cases)
    determinism = _determinism_stress(cases)

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    non_deterministic = sum(1 for r in results if not r["deterministic"])

    per_rule: dict[str, dict[str, int]] = {}
    for r in results:
        rule = r["expected"]["deciding_rule"]
        per_rule.setdefault(rule, {"total": 0, "passed": 0})
        per_rule[rule]["total"] += 1
        if r["passed"]:
            per_rule[rule]["passed"] += 1

    overall_status = (
        "PASS" if failed == 0 and safety["passed"] and determinism["passed"] else "FAIL"
    )

    report = {
        "dataset": str(_DATASET),
        "dataset_kind": "golden policy cases (hand-authored, fixed)",
        "total_cases": total,
        "passed": passed,
        "failed": failed,
        "non_deterministic_cases": non_deterministic,
        "per_rule": per_rule,
        "safety_invariants": safety,
        "determinism_stress": determinism,
        "overall_status": overall_status,
        "case_results": results,
        "scope_note": (
            "This report evaluates policy rule correctness only. It does "
            "not measure, and must not be read as measuring, real-world "
            "recovered revenue, conversion rates, or business impact "
            "(KI-007)."
        ),
    }

    print(f"Decision policy evaluation: {passed}/{total} cases passed ({overall_status})")
    print(f"Safety invariants: {'PASS' if safety['passed'] else 'FAIL'}")
    if safety["failures"]:
        for f in safety["failures"]:
            print(f"  - {f}")
    print(
        f"Determinism ({determinism['repeats_per_case']}x per case): "
        f"{'PASS' if determinism['passed'] else 'FAIL'}"
    )
    if determinism["failures"]:
        for f in determinism["failures"]:
            print(f"  - {f}")
    print("\nPer-rule coverage:")
    for rule, counts in sorted(per_rule.items()):
        print(f"  {rule}: {counts['passed']}/{counts['total']}")
    if failed:
        print("\nFailed cases:")
        for r in results:
            if not r["passed"]:
                print(f"  {r['case_id']}: expected={r['expected']} actual={r['actual']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.json}")

    if overall_status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
