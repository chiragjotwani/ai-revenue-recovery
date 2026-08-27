# Risk API (Phase 2)

## `GET /risk/payments`

Returns every currently at-risk payment (a failed payment with no later
successful payment for the same customer), each with its computed risk
assessment.

```json
[
  {
    "payment_id": "uuid",
    "customer_id": "uuid",
    "external_reference": "string",
    "amount": "4999.00",
    "currency": "INR",
    "failure_reason": "insufficient_funds",
    "consecutive_failures": 1,
    "historical_success_rate": 0.75,
    "risk_score": 0.3033,
    "risk_level": "low"
  }
]
```

## `GET /risk/summary`

Aggregate view for a dashboard.

```json
{
  "at_risk_payment_count": 1,
  "revenue_at_risk": "4999.00",
  "currency_breakdown": { "INR": "4999.00" },
  "risk_level_breakdown": { "low": 1, "medium": 0, "high": 0 }
}
```

`revenue_at_risk` is a naive cross-currency sum -- see
`docs/known-issues.md` KI-006. Use `currency_breakdown` for an accurate
per-currency total.

## Scoring Method

See `backend/app/risk/scoring.py`. Deterministic, rule-based, fully
explainable:

- 40% weight: consecutive failures since the customer's last success
  (saturates at 3)
- 40% weight: failure-reason severity (e.g. `insufficient_funds` = 0.3,
  `fraud_suspected` = 0.95; unlisted reasons default to 0.6)
- 20% weight: `1 - historical_success_rate` for that customer

Buckets: `< 0.34` low, `< 0.67` medium, else high.
