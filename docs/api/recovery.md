# Recovery Case API (Phase 3)

A recovery case tracks one at-risk payment through an explicit lifecycle.
State is only ever changed through a validated transition; every change is
recorded in an append-only history. See
`docs/decisions/ADR-004-explicit-recovery-state-machine.md`.

## States

```
detected -> diagnosing -> diagnosed -> decision_pending
         -> action_scheduled -> action_executed -> observing -> recovered
```

`abandoned` and `failed` are terminal. `recovered` is terminal. A case in a
terminal state has `closed_at` set and accepts no further transitions.

Legal transitions live in `backend/app/recovery/state_machine.py`
(`LEGAL_TRANSITIONS`). Phase 3 only drives `detected -> diagnosing` and the
`-> abandoned` edges in practice; the rest are exercised by later phases.

## `POST /recovery/cases`

Open a recovery case for a failed payment.

### Request body

```json
{ "payment_id": "uuid" }
```

### Responses

- `201 Created` — a new case was opened, in state `detected`.
- `200 OK` — a case already existed for this payment; the existing case is
  returned unchanged (idempotent — `payment_id` is unique).
  ```json
  {
    "id": "uuid",
    "payment_id": "uuid",
    "customer_id": "uuid",
    "state": "detected",
    "opened_at": "ISO 8601 datetime",
    "closed_at": null
  }
  ```
- `404 Not Found` — no payment with that id.
- `409 Conflict` — the payment exists but is not `failed`; only failed
  payments can be recovered.

## `GET /recovery/cases`

List cases, newest first (by `opened_at`).

### Query parameters

- `state` — optional; one of the state values above. Filters to cases
  currently in that state.

### Response

`200 OK` — array of the case object shown above.

## `GET /recovery/cases/{case_id}`

### Responses

- `200 OK` — the case object plus its ordered (oldest first) transition
  history:
  ```json
  {
    "id": "uuid",
    "payment_id": "uuid",
    "customer_id": "uuid",
    "state": "diagnosing",
    "opened_at": "ISO 8601 datetime",
    "closed_at": null,
    "history": [
      {
        "id": "uuid",
        "from_state": null,
        "to_state": "detected",
        "reason": "case opened",
        "actor": "api",
        "created_at": "ISO 8601 datetime"
      },
      {
        "id": "uuid",
        "from_state": "detected",
        "to_state": "diagnosing",
        "reason": null,
        "actor": "api",
        "created_at": "ISO 8601 datetime"
      }
    ]
  }
  ```
- `404 Not Found` — no case with that id.

## `POST /recovery/cases/{case_id}/transitions`

Move a case to a new state.

### Request body

```json
{ "to_state": "diagnosing", "reason": "optional free text" }
```

### Responses

- `200 OK` — transition applied; returns the updated case object. If
  `to_state` is terminal, `closed_at` is now set.
- `404 Not Found` — no case with that id.
- `409 Conflict` — the state machine does not permit
  `current_state -> to_state`. Nothing is written; the case is unchanged
  and no history row is added.
- `422 Unprocessable Entity` — `to_state` is not a valid state value.

## `POST /recovery/cases/{case_id}/diagnose` (Phase 4)

Run the configured reasoning model to diagnose the case's payment failure.
Builds a bounded context, calls the model, validates the output, stores a
`Diagnosis`, and advances the case `detected -> diagnosing -> diagnosed`.
The model only diagnoses — it never decides or acts (ADR-003). See
`docs/ai/diagnosis.md`.

No request body.

### Responses

- `200 OK` — diagnosis stored; returns:
  ```json
  {
    "id": "uuid",
    "outcome": "insufficient_funds",
    "disposition": "retriable_transient",
    "confidence": 0.9,
    "reasoning": "…",
    "recommended_strategy": "retry",
    "recommended_delay_hours": 6,
    "schema_version": "1",
    "model_name": "mock",
    "model_version": "1",
    "prompt_version": "diagnosis_prompt_v1",
    "latency_ms": 0,
    "created_at": "ISO 8601 datetime"
  }
  ```
  `outcome` is one of `insufficient_funds`, `card_expired`, `do_not_honor`,
  `processing_error`, `stolen_card`, `lost_card`, `fraud_suspected`,
  `authentication_required`, `card_not_supported`, `unknown`.
  `disposition` is derived from `outcome`: `retriable_transient`,
  `customer_action_required`, `suspected_fraud`, or `unknown`.
  `recommended_strategy` / `recommended_delay_hours` are advisory only —
  Phase 5's policy engine decides what actually happens.
- `404 Not Found` — no case with that id.
- `409 Conflict` — the case is not in `detected` or `diagnosing` (e.g. it
  is already `diagnosed`).
- `502 Bad Gateway` — the model was unreachable or its output could not be
  validated after a retry. The case is left in `diagnosing`; nothing is
  persisted; the diagnosis can be retried.

## `GET /recovery/cases/{case_id}` — `diagnosis` field (Phase 4)

The case detail response now includes `"diagnosis"`: the latest stored
diagnosis (the object shown above) or `null` if none has run.

See `backend/tests/test_recovery_api.py`,
`backend/tests/test_recovery_state_machine.py`,
`backend/tests/test_diagnosis_api.py`, and `backend/tests/test_ai_*.py` for
the exact behaviour each case exercises, and `docs/database/schema.md` for
the tables.
