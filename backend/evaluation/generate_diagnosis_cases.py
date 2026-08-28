"""Generate the diagnosis evaluation dataset (Section 52).

Deterministic: run with no arguments to (re)write ``diagnosis_cases.json``
next to this file. The JSON file -- not this script -- is the artifact of
record; it is a *stable* evaluation set and must not be edited to make a
model look better (see KI-007). Regenerate only when the taxonomy or the
context shape itself changes.

There is no real payment-failure data available, so every case is
synthetic and its label is assigned here from the scenario we constructed.
The failure-reason vocabulary mirrors common processor decline codes
(Stripe ``decline_code`` / card-network response codes).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT_PATH = Path(__file__).with_name("diagnosis_cases.json")

_BASE = datetime(2026, 1, 1, tzinfo=UTC)

# reason -> (expected outcome, is that outcome a specific/confident one)
_REASON_TO_OUTCOME = {
    "insufficient_funds": "insufficient_funds",
    "card_expired": "card_expired",
    "do_not_honor": "do_not_honor",
    "processing_error": "processing_error",
    "stolen_card": "stolen_card",
    "lost_card": "lost_card",
    "fraud_suspected": "fraud_suspected",
    "authentication_required": "authentication_required",
    "card_not_supported": "card_not_supported",
}
_FRAUD_REASONS = {"stolen_card", "lost_card", "fraud_suspected"}


def _context(
    *,
    ref: str,
    failure_reason: str | None,
    successful: int,
    prior_failures: int,
    consecutive_failures: int,
    evidence: str,
    conflict: bool,
    amount: str = "4999.00",
) -> dict:
    total = successful + prior_failures + 1
    rate = round(successful / total, 4) if total else 0.0
    history = [
        {
            "external_reference": f"{ref}-h{i}",
            "status": "succeeded",
            "amount": amount,
            "currency": "INR",
            "failure_reason": None,
            "occurred_at": (_BASE - timedelta(days=7 * (successful - i))).isoformat(),
        }
        for i in range(successful)
    ]
    return {
        "case_id": "00000000-0000-0000-0000-000000000000",
        "case_state": "diagnosing",
        "customer": {
            "external_id": f"cust-{ref}",
            "tenure_days": 7 * max(successful, 1),
            "total_payments": total,
            "successful_payments": successful,
            "historical_success_rate": rate,
        },
        "payment": {
            "external_reference": ref,
            "amount": amount,
            "currency": "INR",
            "status": "failed",
            "failure_reason": failure_reason,
            "occurred_at": _BASE.isoformat(),
        },
        "failure": {
            "consecutive_failures": consecutive_failures,
            "distinct_prior_failure_reasons": (
                [failure_reason] if failure_reason and prior_failures else []
            ),
            "days_since_last_success": 7 if successful else None,
        },
        "recent_history": history[-10:],
        "previous_interventions": [],
        "applicable_policies": ["A payment may be retried at most 3 times."],
        "evidence_sufficiency": evidence,
        "signals_conflict": conflict,
    }


def build_cases() -> list[dict]:
    cases: list[dict] = []

    # 1. Clear, well-evidenced cases for every known reason, a few variants each.
    for reason, outcome in _REASON_TO_OUTCOME.items():
        for variant, (succ, prior, consec) in enumerate(
            [(3, 0, 1), (5, 1, 2), (8, 2, 3), (2, 0, 1), (4, 1, 1), (6, 0, 1)]
        ):
            conflict = reason in _FRAUD_REASONS and succ >= 3 and (succ / (succ + prior + 1)) >= 0.8
            cases.append(
                {
                    "id": f"{reason}-v{variant}",
                    "context": _context(
                        ref=f"{reason}-v{variant}",
                        failure_reason=reason,
                        successful=succ,
                        prior_failures=prior,
                        consecutive_failures=consec,
                        evidence="sufficient",
                        conflict=conflict,
                    ),
                    "expected_outcome": outcome,
                    "min_confidence": 0.0 if conflict else 0.6,
                    "max_confidence": 0.5 if conflict else 1.0,
                    "expect_unknown": False,
                    "notes": (
                        "fraud flag on an established clean customer -> expect low confidence"
                        if conflict
                        else "clear reason with supporting history"
                    ),
                }
            )

    # 2. Sparse cases: no failure reason, or no history. Expect UNKNOWN.
    for i in range(8):
        cases.append(
            {
                "id": f"sparse-no-reason-{i}",
                "context": _context(
                    ref=f"sparse-nr-{i}",
                    failure_reason=None,
                    successful=i % 4,
                    prior_failures=0,
                    consecutive_failures=1,
                    evidence="sparse",
                    conflict=False,
                ),
                "expected_outcome": "unknown",
                "min_confidence": 0.0,
                "max_confidence": 0.5,
                "expect_unknown": True,
                "notes": "no failure reason -> must not guess a specific cause",
            }
        )
    for i in range(6):
        cases.append(
            {
                "id": f"sparse-no-history-{i}",
                "context": _context(
                    ref=f"sparse-nh-{i}",
                    failure_reason="insufficient_funds",
                    successful=0,
                    prior_failures=0,
                    consecutive_failures=1,
                    evidence="sparse",
                    conflict=False,
                ),
                "expected_outcome": "unknown",
                "min_confidence": 0.0,
                "max_confidence": 0.5,
                "expect_unknown": True,
                "notes": "only one payment on record -> insufficient evidence",
            }
        )

    # 3. Unrecognised reason strings -> UNKNOWN.
    _unmapped = ["gateway_timeout", "currency_mismatch", "unknown_error", "risk_hold"]
    for i, reason in enumerate(_unmapped):
        cases.append(
            {
                "id": f"unmapped-{i}",
                "context": _context(
                    ref=f"unmapped-{i}",
                    failure_reason=reason,
                    successful=4,
                    prior_failures=1,
                    consecutive_failures=1,
                    evidence="sufficient",
                    conflict=False,
                ),
                "expected_outcome": "unknown",
                "min_confidence": 0.0,
                "max_confidence": 0.7,
                "expect_unknown": True,
                "notes": f"reason {reason!r} is not in the taxonomy",
            }
        )

    return cases


def main() -> None:
    cases = build_cases()
    OUT_PATH.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT_PATH}")


if __name__ == "__main__":
    main()
