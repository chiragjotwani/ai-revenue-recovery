# Project State

## Current Phase

Phase 1 — Data Foundation: COMPLETE

## Current Stage

N/A (Phase 1 closed; Phase 2 not yet started)

## Completed Phases

- Phase 0 — Engineering Foundation
- Phase 1 — Data Foundation

## Completed Stages (Phase 1)

- Database models: `Customer`, `Payment` (with `PaymentStatus` enum),
  `IngestionEvent` (append-only event log with idempotency key)
- Alembic migrations configured and initial schema migration applied
  (`backend/migrations/versions/d54257564c3a_initial_schema.py`)
- Ingestion pipeline: `POST /events` validates via Pydantic, is
  idempotent on `idempotency_key` (including a concurrent-write race
  handled via `IntegrityError` recovery), rejects `external_reference`
  reuse under a different idempotency key with 409, and get-or-creates
  customers by `external_id`
- Synthetic dataset script (`backend/scripts/seed_synthetic_data.py`)
  producing the canonical scenario: 3 historically successful payments +
  1 failed payment of 4999.00 (insufficient_funds) for the same customer
- Database tests (`tests/test_models.py`): unique/foreign-key constraints
  verified against real Postgres
- Ingestion tests (`tests/test_ingestion.py`): new event creates records,
  duplicate idempotency key is a no-op, conflicting reference is
  rejected, invalid/non-positive-amount payloads are rejected, customer
  reuse across events
- Full verification: 11/11 tests pass, ruff/format/mypy clean, verified
  end-to-end in fresh Docker containers (migrations run automatically on
  backend container startup; ingestion succeeded across the container
  network)
- CI updated: backend job now provisions a Postgres service and runs
  `alembic upgrade head` before pytest
- Documentation: `docs/database/schema.md`, `docs/architecture.md`
  updated, known-issues updated

## Known Issues

See `docs/known-issues.md`.
- Open: KI-002 (self-hosted model infra undecided, relevant Phase 4+)
- Resolved this phase: KI-004 (psycopg3 async incompatible with Windows'
  default event loop), KI-005 (Postgres port collision with a native
  Windows Postgres service)

## Architecture Decisions

- ADR-001: PostgreSQL as source of truth
- ADR-002: Modular monolith
- ADR-003: LLM cannot directly execute financial actions

## Last Successful Verification

Backend: pytest (11/11), ruff check, ruff format --check, mypy — all
clean. Full docker-compose stack rebuilt from a clean volume and verified
end-to-end (migrations auto-applied on backend startup; `/health` and
`POST /events` both verified through the container network; frontend
rendered live backend status).

## Last Git Commit

(updated after commit — see git log)

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is normally raised to the project owner
for approval before proceeding. For a defined window on 2026-08-27, the
project owner explicitly authorized proceeding autonomously through
Phase 0 completion and into Phase 1 without per-decision approval, with a
combined report at the end of that window.
