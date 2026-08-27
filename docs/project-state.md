# Project State

## Current Phase

Phase 2 — Revenue Risk Detection: COMPLETE

## Current Stage

N/A (Phase 2 closed; Phase 3 not yet started)

## Completed Phases

- Phase 0 — Engineering Foundation
- Phase 1 — Data Foundation
- Phase 2 — Revenue Risk Detection

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

Backend: pytest (22/22), ruff check, ruff format --check, mypy — all
clean. Frontend: tsc, eslint, next build — all clean. Full docker-compose
stack rebuilt from a clean volume and verified end-to-end: migrations
auto-applied, canonical scenario ingested, risk score/level verified by
hand calculation, dashboard rendered live data across the container
network.

## Last Git Commit

(updated after commit — see git log)

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is raised to the project owner for
approval before proceeding.
