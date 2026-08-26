# Database Schema (Phase 1)

Managed by Alembic (`backend/migrations/`). Run `alembic upgrade head` to
apply.

## `customers`

| column      | type          | notes                              |
| ----------- | ------------- | ----------------------------------- |
| id          | uuid, PK      |                                      |
| external_id | varchar(255)  | unique, indexed; source-system id  |
| email       | varchar(320)  |                                      |
| name        | varchar(255)  | nullable                            |
| created_at  | timestamptz   | server default now()               |

## `payments`

| column              | type          | notes                                          |
| ------------------- | ------------- | ------------------------------------------------ |
| id                  | uuid, PK      |                                                    |
| customer_id         | uuid, FK      | -> customers.id                                   |
| external_reference  | varchar(255)  | unique, indexed; one row per payment attempt      |
| amount              | numeric(12,2) |                                                    |
| currency            | varchar(3)    | ISO 4217, stored upper-case                       |
| status              | enum          | pending / succeeded / failed                      |
| failure_reason      | varchar(255)  | nullable                                          |
| occurred_at         | timestamptz   | when the payment event actually happened          |
| created_at          | timestamptz   | server default now(); when we ingested it         |

## `ingestion_events`

Append-only, immutable event log (Section 15 of the engineering prompt).

| column          | type          | notes                                            |
| --------------- | ------------- | --------------------------------------------------- |
| id              | uuid, PK      |                                                       |
| idempotency_key | varchar(255)  | unique, indexed; source-supplied delivery id         |
| event_type      | varchar(100)  | e.g. `payment.failed`                                |
| source          | varchar(100)  | reporting system name                                |
| payload         | jsonb         | the full validated inbound event, for audit/replay   |
| occurred_at     | timestamptz   |                                                       |
| received_at     | timestamptz   | server default now()                                 |
| customer_id     | uuid, FK      | nullable; the customer this event resolved to        |
| payment_id      | uuid, FK      | nullable; the payment this event resolved to         |

## Ingestion Semantics

- `POST /events` is the single entry point for payment lifecycle events.
- Idempotency: a redelivered `idempotency_key` returns the original result
  (`duplicate: true`), never creates a second event or payment row.
- Conflict: an `external_reference` reused under a *different*
  `idempotency_key` is rejected with `409 Conflict` — this indicates a
  source-system bug or a tampered/replayed event, and is never silently
  resolved by overwriting the existing payment.
- Customers are get-or-created by `external_id`.
- See `backend/scripts/seed_synthetic_data.py` for the canonical scenario
  (3 successful payments + 1 failed payment of 4999.00, insufficient
  funds) used as the running example through later phases.
