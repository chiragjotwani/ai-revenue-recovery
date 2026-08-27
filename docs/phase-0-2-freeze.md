# Phase 0–2 Freeze Record

**Date:** 2026-08-27
**Verified against:** `docs/master-loop-engineering-prompt.md` (the committed project
contract) — Sections 19 (Phase 0), 20 (Phase 1), 21 (Phase 2), 38 (canonical
end-to-end scenario), 39 (regression gate).
**Frozen commit:** see `freeze: phases 0-2 verified` in `git log`.

This record documents a complete final verification performed before starting
Phase 3. Every check below was **executed**, not inferred from code inspection.

---

## Environment

| Tool | Version |
|------|---------|
| Docker Engine | 29.5.3 |
| Docker Compose | v5.1.4 |
| Python | 3.13.12 |
| Node.js | v24.18.0 (CI uses 20) |
| Postgres | 16-alpine (container) |
| Redis | 7-alpine (container) |

Backend checks were run from the local venv against the dockerized Postgres
published on host port 5433 (`DATABASE_URL=postgresql+psycopg://arr_user:arr_password@localhost:5433/arr_db`);
GitHub Actions CI runs the identical checks against Postgres on 5432.

---

## Exact commands and results

### Full clean-state rebuild (reproducibility gate)

```
docker compose down -v
docker compose up --build -d
```

- All four images built from clean; `postgres` and `redis` reached `healthy`;
  `backend` and `frontend` started in dependency order.
- Backend container entrypoint ran `alembic upgrade head` automatically:
  `Running upgrade  -> d54257564c3a, initial schema`, then `Application startup complete`.
- `alembic_version` = `d54257564c3a`; tables present: `customers`, `payments`,
  `ingestion_events`, `alembic_version`.

### Backend — lint / format / types / migrations / tests

| Command | Result |
|---------|--------|
| `ruff check .` | `All checks passed!` (exit 0) |
| `ruff format --check .` | `36 files already formatted` (exit 0) |
| `mypy app` | `Success: no issues found in 26 source files` (exit 0) |
| `alembic upgrade head` (clean DB, via container) | applied `d54257564c3a` |
| `alembic current` | `d54257564c3a (head)` |
| `alembic check` | `No new upgrade operations detected.` — no model/migration drift |
| `pytest -q` | **22 passed** (local venv, real Postgres) |
| `pytest -q` (against freshly rebuilt clean stack) | **22 passed** |

Test breakdown (22):
- `test_health.py` — 2 (liveness status + response shape)
- `test_models.py` — 3 (unique `external_id`, FK requires existing customer,
  unique `external_reference`) — all assert real `IntegrityError`
- `test_ingestion.py` — 7 (create; duplicate idempotency key is a no-op;
  conflicting reference + new key → 409; missing amount → 422; non-positive
  amount → 422; existing customer reused)
- `test_risk_scoring.py` — 6 (weights sum to 1.0; score bounded [0,1] and
  monotonic worst>best; more consecutive failures → higher; higher success
  rate → lower; unknown/None reason → default severity 0.6; bucket edges)
- `test_risk_api.py` — 5 (no-history failure is at risk; failure superseded by
  later success is not; consecutive failures counted since last success +
  historical rate = 1/3; summary aggregates amount and levels; empty summary)

### Frontend — lint / types / build

| Command | Result |
|---------|--------|
| `npm run lint` (eslint) | clean (exit 0) |
| `rm -rf .next && npx next typegen` | `Types generated successfully` |
| `npx tsc --noEmit` | clean (exit 0) |
| `npm run build` | `Compiled successfully`; routes `/` (ƒ), `/risk` (ƒ), `/_not-found` (○) |

### Runtime end-to-end (dockerized stack, container network)

| Check | Result |
|-------|--------|
| `curl localhost:8000/health` | `{"status":"ok","environment":"development"}` |
| `fetch('http://backend:8000/health')` from **inside** frontend container | `{"status":"ok","environment":"development"}` — no cross-container `localhost` regression |
| `curl localhost:3000` (homepage SSR) | renders `Backend status: ok (development)` |
| `curl localhost:8000/risk/summary` (empty) | `at_risk_payment_count: 0`, `revenue_at_risk: "0"` |
| `curl localhost:8000/risk/payments` (empty) | `[]` |
| `python scripts/seed_synthetic_data.py` | 4 events `created` |
| re-run seed | 4 events `duplicate`; row counts unchanged (1 customer, 4 payments, 4 events) — seed is idempotent |
| `POST /events` reusing `seed-pay-failure-0` with a new idempotency key | `409` |
| `POST /events` with `{"bad":"payload"}` | `422` |
| `GET /nonexistent` | `404` |
| `GET /docs` | `200` |
| `curl localhost:3000/risk` (SSR, cross-container live data) | renders "Revenue Risk Dashboard", `seed-pay-failure-0`, `4999.00 INR`, `insufficient_funds` |

### Canonical scenario (Section 38) — hand-verified math

Seeded: one customer, 3 successful INR 4999.00 payments, then 1 failed
INR 4999.00 payment, `insufficient_funds`.

`GET /risk/payments` returned for the failed payment:
`consecutive_failures=1`, `historical_success_rate=0.75`, `risk_score=0.3033`,
`risk_level="low"`.

Hand check:
`consecutive_factor = min(1/3, 1) = 0.3333`;
`reason_factor(insufficient_funds) = 0.30`;
`unreliability = 1 - 0.75 = 0.25`;
`score = 0.3333·0.4 + 0.30·0.4 + 0.25·0.2 = 0.13333 + 0.12 + 0.05 = 0.30333`
→ rounded to 4 dp = **0.3033**; `< 0.34` → **low**. Matches.

`GET /risk/summary`: `at_risk_payment_count=1`, `revenue_at_risk="4999.00"`,
`currency_breakdown={"INR":"4999.00"}`, `risk_level_breakdown={low:1,medium:0,high:0}`.

### Repository hygiene

- `git ls-files` contains no `node_modules/`, `.venv/`, `.next/`,
  `__pycache__/`, `*.egg-info/`, `tsconfig.tsbuildinfo`, or `.env`.
- `.env` (root) is git-ignored; it holds only local dev defaults identical to
  `.env.example` (no real secrets).
- CI (`.github/workflows/ci.yml`) is green on every commit to `main`
  (`phase-0`, `phase-1`, `phase-2`).

---

## Phase 0 — Engineering Foundation — **PASS**

| Acceptance criterion (Section 19) | Status |
|---|---|
| Repository structure (backend + frontend + docs) | PASS |
| Backend skeleton (FastAPI, `create_app`, routers) | PASS |
| Frontend skeleton (Next.js 16 App Router, TS, Tailwind) | PASS |
| Docker setup (`backend/Dockerfile`, `frontend/Dockerfile` multi-stage) | PASS — both build from clean |
| PostgreSQL + Redis via `docker-compose.yml` with healthchecks + ordered `depends_on` | PASS |
| Environment configuration (`app/core/config.py`, pydantic-settings, single entry point) | PASS |
| Health endpoint (`GET /health`) | PASS — 2 tests |
| Linting / formatting / type checking (ruff, mypy strict) | PASS |
| Tests | PASS — suite runs |
| GitHub Actions CI | PASS — YAML present, equivalents executed locally, remote runs green |
| README | PASS |

Bugs fixed historically (all verified still resolved this run): KI-001
(frontend `NEXT_PUBLIC_` baked in at build time → server-only `API_BASE_URL`),
KI-003 (`next typegen` before `tsc` in CI).

Known limitation (cosmetic, non-blocking): `frontend/src/app/layout.tsx`
still carries the default `metadata` title "Create Next App". No behavioral
impact; not part of any Phase 0–2 acceptance criterion.

---

## Phase 1 — Data Foundation — **PASS**

| Acceptance criterion (Section 20) | Status |
|---|---|
| Database models (`Customer`, `Payment`, `IngestionEvent`) with FKs, unique constraints, indexes | PASS |
| Migrations (Alembic, single `d54257564c3a` initial schema) | PASS — applies from clean; `alembic check` shows no drift |
| Event model (immutable, unique `idempotency_key`, traceability FKs to customer/payment) | PASS |
| Ingestion pipeline (`POST /events`, single entry point) | PASS |
| Validation (Pydantic: `amount > 0`, 3-char currency, required fields → 422) | PASS — 3 tests |
| Idempotency (duplicate key → no-op duplicate; reference reuse under new key → 409; concurrent `IntegrityError` recovery branch) | PASS — 3 tests + runtime 409; concurrency branch present, see limitation |
| Synthetic dataset (`scripts/seed_synthetic_data.py` — canonical scenario) | PASS — created + idempotent re-run verified at runtime |
| Database tests | PASS — `test_models.py` |
| Ingestion tests | PASS — `test_ingestion.py` |
| Complete ingestion flow verified | PASS — event → customer + payment + event row, counts asserted |

Bugs fixed historically (verified still resolved): KI-004 (psycopg3 async vs
Windows ProactorEventLoop → `windows_compat` policy + `run_dev.py`), KI-005
(host Postgres port collision → container host port remapped to 5433).

Known limitations (non-blocking, not correctness defects):
- The concurrent-write `IntegrityError` recovery path in
  `ingest_payment_event` (re-check after rollback) has no dedicated automated
  test — it requires true concurrency to exercise. The single-threaded
  idempotency and 409 paths are tested; the recovery branch is straightforward
  re-read logic.
- `seed_synthetic_data.py` has no automated smoke test (verified manually
  each rebuild).
- **Scope note:** this task's brief referenced Phase 1 artifacts (train/
  validation/test splits, ground-truth outcome-label columns, a frozen ML
  evaluation dataset). These are **not** part of the frozen Phase 1 contract
  in `master-loop-engineering-prompt.md` and were never implemented. The
  project's AI approach (ADR-003, Section 7) is inference against pre-trained
  self-hosted open-weight LLMs — there is no model training, hence no
  train/val/test split. LLM-based diagnosis is Phase 4, and uses a benchmark
  dataset built there. This is a documentation mismatch between the task brief
  and the project contract, not a missing deliverable. Flagged for owner
  decision before Phase 3 (see below).

---

## Phase 2 — Revenue Risk Detection — **PASS**

| Acceptance criterion (Section 21) | Status |
|---|---|
| Risk features (`consecutive_failures` since last success, `historical_success_rate`) computed from Postgres only | PASS |
| Rule-based detection (failed payment with no later success for that customer) | PASS — 2 tests |
| Risk score (deterministic weighted 0.4/0.4/0.2, bounded [0,1], low/medium/high) | PASS — 6 tests |
| Revenue-at-risk calculation (per-currency `currency_breakdown`; `revenue_at_risk` naive sum, documented limitation KI-006) | PASS — 1 test |
| Risk API (`GET /risk/payments`, `GET /risk/summary`) | PASS |
| Risk dashboard (frontend `/risk`, SSR, text-labelled level badges, empty state) | PASS — rendered live cross-container data |
| Tests | PASS — 11 new (22 total) |
| Full regression before commit | PASS |

This is a deterministic, non-AI baseline: it represents what the platform
does **without** any model. No ML, no LLM. It is a defensible reference point
for later before/after comparison (it is not an artificially weak baseline —
it uses real payment history and a documented severity model).

Known limitations (non-blocking):
- KI-006: `revenue_at_risk` sums across currencies with no FX conversion.
  Accurate for the single-currency canonical scenario; `currency_breakdown`
  is always correct. Documented, not hidden. No multi-currency requirement
  exists in Phase 2.
- No automated frontend test for `/risk` (no frontend test runner is
  configured in the project yet); verified by rendering the SSR HTML.

---

## Integration Phase 0 → 1 → 2 — **PASS**

Event ingested via `POST /events` (Phase 1) → materialized `Customer` +
`Payment` + `IngestionEvent` rows in Postgres (Phase 0 infra) → `GET
/risk/payments` (Phase 2) reads those rows, computes features and score →
frontend `/risk` (Phase 0 frontend) renders the Phase 2 output via
server-side fetch across the container network. Verified end-to-end this run
with the canonical scenario.

---

## Full regression gate (Section 39) — **PASS**

backend unit + integration + DB + state-of-scoring tests (22), frontend lint,
frontend type check, frontend build, ruff lint, ruff format check, mypy
strict, alembic upgrade from clean, alembic drift check, dockerized
end-to-end happy + failure paths — all green. No failures, no skips, no
xfails.

---

## Known limitations carried forward (none are unresolved correctness bugs)

1. **KI-002** — self-hosted LLM infrastructure (GPU) undecided. Relevant from
   Phase 4; does not block Phase 3.
2. **KI-006** — `revenue_at_risk` has no cross-currency FX conversion.
   Documented; `currency_breakdown` is the accurate field.
3. Concurrent-ingestion `IntegrityError` recovery branch has no dedicated
   automated test (requires real concurrency).
4. `seed_synthetic_data.py` has no automated smoke test.
5. No frontend test runner / no automated frontend tests.
6. `layout.tsx` default `metadata` title ("Create Next App") — cosmetic.

---

## Phase 3 — NOT STARTED (blocked pending owner decision)

Verification gate for Phases 0–2 has **passed**. Phase 3 was **not** started
because the definition of Phase 3 in this task's brief conflicts with the
frozen project contract, which is a STOP condition:

| This task brief | Frozen contract (`master-loop-engineering-prompt.md` §18, §22) |
|---|---|
| Phase 3 = "AI Risk & Diagnosis": training pipeline, feature engineering, model training, model persistence/versioning, inference service, baseline comparison, leakage prevention | Phase 3 = "Recovery Case Management": `RecoveryCase`, recovery states, transition engine, idempotency, recovery APIs, state-machine tests. No ML. |
| AI = train a model, evaluate lift vs. baseline on held-out test set | AI (ADR-003, §7, §8) = **inference only** against pre-trained self-hosted open-weight LLMs (Qwen3-30B-A3B, Nemotron-3-Nano) behind a provider abstraction; models are **benchmarked, never trained**; LLM diagnosis is **Phase 4**; the LLM may never execute financial actions |

Per the project's standing process rule (raise any decision carrying
uncertainty) and this brief's own STOP conditions ("a Phase 3 requirement
conflicts with the frozen architecture", "do not silently invent an
implementation decision"), the next step is an explicit owner decision on
which Phase 3 definition governs. Options:

- **A. Follow the frozen contract:** Phase 3 = Recovery Case Management
  (state machine). LLM diagnosis stays in Phase 4. No training pipeline is
  built (the architecture has no trained model).
- **B. Re-scope the project** to the ML paradigm in this brief (train/val/test
  splits, a trained risk/diagnosis model, lift vs. baseline). This
  contradicts ADR-003 and Section 7 and would require rewriting the phase
  plan and the AI model policy, plus adding label columns and dataset splits
  retroactively to Phase 1.
- **C. Hybrid:** keep the frozen phase order, but add a formal frozen
  evaluation dataset + baseline-metrics harness now (as a Phase 2.5) so that
  Phase 4's LLM diagnosis can be measured against the Phase 2 rule-based
  baseline on a fixed population.
