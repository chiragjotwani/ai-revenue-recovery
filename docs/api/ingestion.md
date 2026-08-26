# Ingestion API (Phase 1)

## `POST /events`

Ingest a payment lifecycle event. This is the platform's single entry
point for payment data.

### Request body

```json
{
  "idempotency_key": "string, required, unique per delivery",
  "event_type": "payment.succeeded | payment.failed | payment.pending",
  "source": "string, required (reporting system name)",
  "occurred_at": "ISO 8601 datetime",
  "customer": {
    "external_id": "string, required",
    "email": "valid email, required",
    "name": "string, optional"
  },
  "payment": {
    "external_reference": "string, required, unique per attempt",
    "amount": "decimal > 0, required",
    "currency": "3-letter ISO 4217 code, required",
    "failure_reason": "string, optional"
  }
}
```

### Responses

- `201 Created` — event processed (see `duplicate` field below).
  ```json
  {
    "event_id": "uuid",
    "customer_id": "uuid",
    "payment_id": "uuid",
    "duplicate": false
  }
  ```
  `duplicate: true` means this `idempotency_key` was already processed;
  the returned ids point at the original records, and no new rows were
  created.
- `409 Conflict` — the `external_reference` is already in use under a
  *different* `idempotency_key`. This is never silently resolved.
- `422 Unprocessable Entity` — schema validation failed (e.g. missing
  field, non-positive amount, invalid email/currency).

See `docs/database/schema.md` for the underlying tables and
`backend/tests/test_ingestion.py` for the exact behavior each of the
above cases exercises.
