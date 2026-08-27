# Project State

## Current Phase

Phases 0–2: FROZEN (verified 2026-08-27 — see `docs/phase-0-2-freeze.md`).
Phase 3 not started — blocked pending an owner decision on the Phase 3
definition (task brief conflicts with the frozen contract; see the freeze
record's "Phase 3 — NOT STARTED" section).

## Current Stage

N/A (Phase 0–2 freeze gate passed; Phase 3 not started)

## Completed Phases

- Phase 0 — Engineering Foundation (frozen)
- Phase 1 — Data Foundation (frozen)
- Phase 2 — Revenue Risk Detection (frozen)

## Completed Stages (Phase 2)

- Risk features (`backend/app/risk/features.py`): consecutive failures
  since a customer's last success, historical success rate, computed
  from Postgres only
- Rule-based deterministic scoring (`backend/app/risk/scoring.py`):
  weighted combination of consecutive failures, failure-reason severity,
  and historical unreliability into a `[0, 1]` score with low/medium/high
  buckets. No ML/LLM involved at this phase.
- Revenue-at-risk detection rule: a failed payment with no later
  successful payment for the same customer (`backend/app/risk/service.py`)
- Risk API: `GET /risk/payments`, `GET /risk/summary`
  (`docs/api/risk.md`)
- Risk dashboard: frontend route `/risk` (stat tiles + at-risk payments
  table, status-colored risk-level badges with text labels, not
  color-only)
- Tests: `test_risk_scoring.py` (pure-function unit tests: weight sum,
  bounds, monotonicity, bucket edges), `test_risk_api.py` (integration
  against real Postgres: no-history case, superseded-failure exclusion,
  consecutive-failure counting, summary aggregation, empty case) — 11 new
  tests, 22 total passing
- Fixed a real pre-existing type bug from Phase 1 caught by mypy while
  building this phase: `Payment.amount` was annotated `Mapped[str]` for a
  `Numeric` column that actually yields `Decimal` at runtime
- Full verification: 22/22 tests pass, ruff/format/mypy clean, frontend
  tsc/lint/build clean, verified end-to-end in freshly rebuilt Docker
  containers (ingested the canonical scenario through the container
  network, confirmed risk score/level match hand-computed expected
  values, dashboard rendered live data via SSR)
- Documentation: `docs/api/risk.md`, `docs/architecture.md` updated,
  known-issues updated (KI-006: documented, not fixed — no FX source
  exists to sum multiple currencies correctly, not required yet)

## Known Issues

See `docs/known-issues.md`.
- Open: KI-002 (self-hosted model infra undecided, relevant Phase 4+),
  KI-006 (revenue_at_risk has no FX conversion across currencies —
  documented limitation, not a defect against any current requirement)
- Resolved: KI-001, KI-003, KI-004, KI-005

## Architecture Decisions

- ADR-001: PostgreSQL as source of truth
- ADR-002: Modular monolith
- ADR-003: LLM cannot directly execute financial actions

## Last Successful Verification

2026-08-27 Phase 0–2 freeze gate (full detail in
`docs/phase-0-2-freeze.md`). Backend: pytest 22/22 (local venv + against a
from-clean `docker compose down -v && up --build`), ruff check, ruff
format --check, mypy strict, `alembic upgrade head` from clean,
`alembic check` (no drift) — all clean. Frontend: eslint, `next typegen`,
`tsc --noEmit`, `next build` — all clean. Dockerized stack verified
end-to-end: migrations auto-applied on container start, cross-container
`backend:8000` reachable from the frontend container, canonical scenario
seeded (and idempotent on re-run), risk score 0.3033/"low" verified by
hand calculation, `/risk` dashboard rendered live data via SSR, failure
paths (409 / 422 / 404) confirmed. CI green on `main`.

## Last Git Commit

`freeze: phases 0-2 verified` — see `git log`.

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is raised to the project owner for
approval before proceeding.
