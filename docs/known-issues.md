# Known Issues

This file tracks unresolved issues and any explicitly authorized bypasses.
Per project policy, nothing here may be silently bypassed — every entry
must record what, why, and impact.

## Open

### KI-002: Self-hosted model infrastructure undecided (relevant from Phase 4)

- **What**: The engineering prompt specifies self-hosted open-weight models
  (Qwen3-30B-A3B-Instruct-2507, Nemotron 3 Nano 30B-A3B) as reasoning
  providers. These are ~30B-parameter models: even at 4-bit they need
  ~17–18 GB just for weights, more for throughput.
- **Hardware finding (2026-08-28)**: the available GPU is an NVIDIA
  RTX 4050, **6 GB VRAM**. The 30B candidates do not fit in it. Realistic
  paths: (a) a small model (~3–4 B, e.g. `qwen3:4b` via Ollama) locally on
  the 6 GB card for development; (b) the 30B-A3B ("3 B active" MoE) on
  CPU + ~20 GB system RAM via llama.cpp — slow but workable for a
  background call; (c) a rented cloud GPU (24–80 GB) for the actual
  Qwen-vs-Nemotron benchmark. See `docs/ai/local-model-setup.md`.
- **Impact**: Phase 4 is built and fully tested against the deterministic
  `MockProvider` (the default). `QwenProvider` / `NemotronProvider` are
  implemented as real config-gated OpenAI-compatible HTTP clients and are
  unit-tested with a faked transport, but a **meaningful** model benchmark
  (Section 52) needs a real endpoint and therefore this decision.
- **Resolution plan**: pick a hosting path before doing model selection.
  The provider code and the benchmark runner are ready; only
  `AI_QWEN_BASE_URL` / `AI_NEMOTRON_BASE_URL` need to point somewhere real.
- **Status**: Not bypassed — Phase 4 does not depend on it; model
  *selection* does.

### KI-007: The diagnosis evaluation set is synthetic; benchmark accuracy is not real-world (Phase 4)

- **What**: There is no real payment-failure data. Every case in
  `backend/evaluation/diagnosis_cases.json` is synthetic and its label was
  assigned by the generator from the scenario it constructs. The
  `MockProvider` and the labels share the same reason→outcome logic, so
  `mock` scores 1.0 on every metric by construction.
- **Impact**: `benchmark_diagnosis.py` measures schema compliance,
  hallucination behaviour on sparse cases, confidence-band adherence,
  latency, and throughput — and *agreement with our synthetic labels*, not
  real diagnostic accuracy. It is still a valid instrument for comparing
  two real models against each other and for regression across prompt/model
  versions.
- **Resolution plan**: real accuracy validation comes only with live
  recovery-outcome data (Phase 8+). Until then the evaluation set is
  **fixed** and must not be edited to move a score.
- **Status**: Documented limitation, not a defect. No Phase 4 requirement
  is bypassed.

## Resolved

### KI-006: `revenue_at_risk` naively sums across currencies (Phase 2) — ACKNOWLEDGED, NOT A BUG

- **What**: `GET /risk/summary`'s `revenue_at_risk` field is a plain sum of
  amounts across all at-risk payments regardless of currency. There is no
  FX conversion source in this phase, so mixing e.g. INR and USD in that
  one number would be misleading.
- **Impact**: currently none in practice (single-currency canonical
  scenario), but this will misrepresent totals the moment multiple
  currencies are actually at risk simultaneously.
- **Mitigation shipped now**: `currency_breakdown` (per-currency totals)
  is always accurate and is what `docs/api/ingestion.md`-style consumers
  should use; `revenue_at_risk` is documented in
  `backend/app/risk/service.py::get_risk_summary` as only meaningful for
  a single currency.
- **Resolution plan**: revisit if/when multi-currency volume becomes real
  (introduce an FX rate source and convert to a reporting currency).
  Deliberately not solved now -- no exchange-rate source exists yet and
  guessing one would be premature (Section 44/45 discipline).
- **Status**: Documented limitation, not silently hidden. Not a bypass of
  a requirement -- Phase 2 has no multi-currency requirement.

### KI-005: Postgres port collision with a native Windows Postgres service (Phase 1) — RESOLVED

- **What**: Alembic migration generation and local test runs failed with
  `password authentication failed for user "arr_user"`, even though the
  dockerized Postgres had the correct credentials. Root cause: a native
  Windows Postgres service (`postgres.exe`) was already bound to port
  5432 on the host, so host-side connections were reaching that instance
  instead of the Docker container.
- **Fix**: remapped the dockerized Postgres's published host port to 5433
  (`POSTGRES_PORT=5433` in `.env.example` / `docker-compose.yml`). This
  only changes the host-published port; containers still reach Postgres
  internally at `postgres:5432`, and GitHub Actions CI (which has no such
  native service) still uses 5432 directly.
- **Status**: Resolved. Deliberately did not touch the pre-existing native
  Postgres service, which may belong to unrelated work on this machine.

### KI-004: psycopg3 async mode incompatible with Windows' default event loop (Phase 1) — RESOLVED

- **What**: Running the backend or its test suite on Windows raised
  `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to
  run in async mode`, because psycopg3's asyncio driver requires
  `SelectorEventLoop`, not Windows' default `ProactorEventLoop`.
- **Fix**: added `app/core/windows_compat.py::apply_windows_event_loop_policy()`
  (a no-op on non-Windows platforms), invoked at the top of
  `tests/conftest.py`. For running the live server outside Docker, plain
  `uvicorn app.main:app` creates its event loop *before* importing the app
  module, so the fix must be applied even earlier; `scripts/run_dev.py`
  does this and must be used instead of a raw `uvicorn` invocation when
  developing directly on Windows. Neither of these affects the Linux
  Docker containers, which use the Dockerfile's plain `uvicorn` command.
- **Status**: Resolved.

### KI-003: Frontend type check failed in GitHub Actions CI (Phase 0) — RESOLVED

- **What**: The first CI run on `main` failed the frontend `Type check`
  step with `Cannot find name 'LayoutProps'`. This type is generated by
  Next.js into `.next/types/` and had only ever been present locally
  because `next build`/`next dev` had already run there. A fresh CI
  checkout has no `.next/` directory, so `tsc --noEmit` ran against
  incomplete generated types.
- **Fix**: added an explicit `npx next typegen` step in
  `.github/workflows/ci.yml` before the `tsc --noEmit` step, and documented
  the same step in the README's local verification instructions.
- **Verified**: reproduced locally by deleting `frontend/.next` and running
  `npx next typegen && npx tsc --noEmit` — passes clean.
- **Status**: Resolved.

### KI-001: Docker daemon verification pending (Phase 0) — RESOLVED

- **What**: `docker-compose.yml` (postgres, redis, backend, frontend) was
  built and run end-to-end via `docker compose up --build`.
- **Found during verification**: the frontend's backend health check used
  `NEXT_PUBLIC_API_BASE_URL`, which Next.js inlines at Docker **build**
  time. Inside the frontend container this resolved to
  `http://localhost:8000`, which points at the frontend container itself,
  not the backend service — so the health check always failed across
  containers despite working in local `npm run dev`.
- **Fix**: switched to a server-only `API_BASE_URL` env var (no
  `NEXT_PUBLIC_` prefix), read at container runtime since the fetch always
  happens in a Server Component. `docker-compose.yml` now sets
  `API_BASE_URL=http://backend:8000` for the frontend service explicitly.
- **Verified**: `docker compose up --build` succeeded; `curl
  localhost:8000/health` returned
  `{"status":"ok","environment":"development"}`; `curl localhost:3000`
  rendered "Backend status: ok (development)" confirming cross-container
  connectivity; stack torn down cleanly with `docker compose down`.
- **Status**: Resolved.
