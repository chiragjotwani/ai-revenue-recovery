# Database Schema

Managed by Alembic (`backend/migrations/`). Run `alembic upgrade head` to
apply. Phase 1 tables: `customers`, `payments`, `ingestion_events`.
Phase 3 tables: `recovery_cases`, `recovery_case_transitions`.
Phase 4 table: `diagnoses`.

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

## `recovery_cases` (Phase 3)

One row per at-risk payment being worked (`payment_id` is unique). `state`
is only ever changed through `app/recovery/service.py::transition_case`,
which validates against the state machine
(`app/recovery/state_machine.py`). See
`docs/decisions/ADR-004-explicit-recovery-state-machine.md`.

| column      | type          | notes                                                     |
| ----------- | ------------- | --------------------------------------------------------- |
| id          | uuid, PK      |                                                          |
| payment_id  | uuid, FK      | -> payments.id; **unique**, indexed                       |
| customer_id | uuid, FK      | -> customers.id; indexed                                  |
| state       | enum          | `recovery_case_state`; indexed; see values below          |
| opened_at   | timestamptz   | server default now()                                      |
| closed_at   | timestamptz   | nullable; set when the case enters a terminal state       |
| created_at  | timestamptz   | server default now()                                      |
| updated_at  | timestamptz   | server default now(), ON UPDATE now()                     |

`recovery_case_state` values (Section 16): `detected`, `diagnosing`,
`diagnosed`, `decision_pending`, `action_scheduled`, `action_executed`,
`observing`, `recovered`, `abandoned`, `failed`. Terminal: `recovered`,
`abandoned`, `failed`.

## `recovery_case_transitions` (Phase 3)

Append-only history of every state change (Section 15/16). Never updated or
deleted by application code.

| column      | type          | notes                                                     |
| ----------- | ------------- | --------------------------------------------------------- |
| id          | uuid, PK      |                                                          |
| case_id     | uuid, FK      | -> recovery_cases.id; indexed                             |
| from_state  | enum          | `recovery_case_state`; NULL only for the initial `detected` row |
| to_state    | enum          | `recovery_case_state`                                     |
| reason      | varchar(255)  | nullable; free-text why                                   |
| actor       | varchar(100)  | what caused it, e.g. `api`, `system:open`                 |
| created_at  | timestamptz   | server default now()                                      |

## `diagnoses` (Phase 4)

One row per diagnosis run for a recovery case (a case may be diagnosed more
than once — e.g. a retry after a failed model call; the latest by
`created_at` is the current one). `outcome` / `disposition` /
`recommended_strategy` are stored as strings, validated at the application
layer (`app/ai/schema.py`), not as Postgres enums — see ADR-005.

| column                  | type          | notes                                            |
| ----------------------- | ------------- | ------------------------------------------------ |
| id                      | uuid, PK      |                                                  |
| case_id                 | uuid, FK      | -> recovery_cases.id; indexed                     |
| outcome                 | varchar(50)   | specific cause; `unknown` is valid               |
| disposition             | varchar(50)   | routing category, derived from `outcome`          |
| confidence              | numeric(4,3)  | 0.000–1.000                                       |
| reasoning               | text          | short rationale                                   |
| recommended_strategy    | varchar(50)   | advisory only (Phase 5 decides)                   |
| recommended_delay_hours | integer       | nullable                                          |
| schema_version          | varchar(10)   | diagnosis schema version (`"1"`)                  |
| model_name              | varchar(100)  | e.g. `mock`, `qwen`                               |
| model_version           | varchar(100)  | model/build id reported by the provider           |
| prompt_version          | varchar(100)  | e.g. `diagnosis_prompt_v1`                        |
| latency_ms              | integer       | model call latency                               |
| created_at              | timestamptz   | server default now()                             |

## Recovery Case Semantics

- `POST /recovery/cases` opens a case for a `failed` payment in state
  `detected`; a repeat call returns the existing case (`200`) instead of
  creating a second.
- State changes go only through the transition service. An illegal
  transition raises and writes nothing (`409` at the API).
- Entering a terminal state sets `closed_at`; no further transitions are
  accepted.
- See `docs/api/recovery.md` for the endpoints.

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
