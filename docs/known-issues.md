# Known Issues

This file tracks unresolved issues and any explicitly authorized bypasses.
Per project policy, nothing here may be silently bypassed — every entry
must record what, why, and impact.

## Open

### KI-010: No dedicated test database — `DATABASE_URL` defaults to the shared dev Postgres (Phase 7)

- **What**: `app/core/config.py::Settings.database_url` defaults to the
  same Postgres instance/database the Docker dev stack uses
  (`localhost:5433/arr_db`), and `tests/conftest.py::_clean_database`
  truncates every table before each test. Running the host pytest suite
  with no `DATABASE_URL` override therefore wipes whatever dev/browser-QA
  fixture data exists in `arr_db`.
- **Discovered**: Phase 7 session (2026-09-01) — running the full backend
  suite as part of routine phase-boundary verification silently destroyed
  the Phase 5/6 browser-QA fixture case that had just been manually
  verified, because the "run the full suite" step and the "don't touch
  the dev DB" expectation were never reconciled in the project's own
  tooling.
- **Mitigation used since (Phase 7, Phase 8)**: a second database
  (`arr_test_db`) was created ad hoc inside the same Postgres instance
  each session, and all test runs since have been executed with
  `DATABASE_URL` exported to point at it. This protects dev fixtures but
  is a manual, session-local workaround — it is not committed anywhere
  (`.env.test` does not exist), not documented in the README/CI, and
  relies on whoever runs the suite remembering to set it.
- **Impact**: any contributor (or future session) that runs `pytest`
  without first exporting `DATABASE_URL` will still silently truncate the
  shared dev database, including any manually-seeded demo/fixture data.
- **Resolution plan**: add a committed `.env.test` (or an explicit
  `TEST_DATABASE_URL` read by `conftest.py`) pointing at a dedicated test
  database by default, so the safe behavior is automatic rather than
  something every session must remember to opt into.
- **Status**: Open. Not silently worked around — documented here and in
  the Phase 7/8 session reports each time it was mitigated.

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
- **Update (2026-08-29, Phase 4.1)**: path (a) was exercised — a local
  Ollama server running `qwen3:4b-instruct-2507-q8_0` (~4.3 GB) on the
  6 GB card. `QwenProvider` was driven against it for real for all 8
  Workstream B3 scenarios (`tests/test_ai_real_model.py`); 7/8 passed on
  the first parametrized run, the 8th (`insufficient_funds`) failed on a
  transient Ollama-side `500` and passed cleanly on isolated retry —
  consistent with a cold-start/load hiccup in Ollama itself, not a defect
  in the provider client. This validates the *integration contract*
  (transport, schema validation, safeguards) against a real model; it is
  still not a diagnostic-accuracy benchmark (that needs an independent
  ground-truth set — see KI-007) and the 30B Qwen-vs-Nemotron comparison
  the original engineering prompt asked for still needs the 24–80 GB
  cloud-GPU path (c), not attempted.
- **Status**: Partially resolved — a real model has been exercised in
  development; the production hosting decision for the 30B-class
  benchmark is still open.

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
- **Phase 5 extension (added 2026-08-31)**: the same discipline applies to
  `backend/evaluation/decision_cases.json` /
  `scripts/benchmark_decision_policy.py` (Phase 5H). That harness checks
  the deterministic policy engine against its own hand-authored golden
  cases — a specification check, not a real-accuracy or real-revenue
  measurement. It must never be read as evidence of recovered revenue or
  production conversion rates.
- **Status**: Documented limitation, not a defect. No Phase 4 requirement
  is bypassed.

## Resolved

### KI-012: Phase 6 action execution had no real or simulated external side effect (Phase 6) — RESOLVED

- **What (original gap, present since the original Phase 6 commit,
  2026-09-01)**: `app/decision/actions.py::execute_action` recorded every
  approved strategy other than `no_action`/`manual_review` (i.e. `retry`,
  `request_payment_method_update`, `contact_customer`) as
  `ActionExecutionOutcome.DEFERRED_NO_INTEGRATION` and stopped there — no
  payment-provider or customer-messaging integration, real or simulated,
  existed anywhere in this repository. This was disclosed honestly in the
  module's own docstring at the time, not hidden, but it meant the
  DETECT→ACT→OBSERVE loop was not genuinely closed: Phase 7's
  `observe_outcome` could only ever classify a case as `recovered` when an
  unrelated, independently-ingested `payment.succeeded` event happened to
  arrive for the same customer — never because this system's own action
  caused anything. A full repository audit (2026-09-03, prior to this fix)
  identified this as the single most consequential gap against the
  project's own phase specification, ahead of the buildathon demo.
- **Fix (2026-09-03)**: a deterministic, explicitly SIMULATED execution
  layer — `app/decision/providers.py` (`SimulatedPaymentProvider`, a pure
  function of `(failure_reason, attempt_no)`, no network call) and
  `app/decision/executors.py` (`RetryExecutor` / `PaymentLinkExecutor` /
  `NotificationExecutor`, one per real-side-effect strategy). Only
  `app/decision/actions.py::execute_action` can reach these — ADR-003's
  boundary (the LLM/policy engine can never invoke a provider) is
  unaffected structurally, not just by convention. A bounded (`RETRY_CAP`,
  the same constant `app.decision.policy` already defines) multi-attempt
  loop lives entirely inside `execute_action`; a simulated success creates
  a new `Payment` + `IngestionEvent` row (the identical shape any other
  payment source produces) with an explicit causal link
  (`RecoveryActionExecution.resulting_payment_id`) to what Phase 7 later
  reads as evidence. Phase 7's `observe_outcome` and Phase 8's measurement
  service were **not modified** — both already worked correctly against
  real evidence; they simply had none to observe before this fix.
- **Verified**: `tests/test_canonical_recovery_flow.py::test_canonical_recovery_flow_causes_revenue_recovery`
  drives the full DETECT→DIAGNOSE→DECIDE→SCHEDULE→EXECUTE→SIMULATED
  PROVIDER→OBSERVE→RECOVERED→MEASURE chain for the canonical ₹4,999
  insufficient-funds scenario and asserts the causal link at every step
  (the executed action's `resulting_payment_id` equals the observation's
  `evidence_payment_id`) — not merely that a recovery eventually appears.
  New coverage in `tests/test_action_executor.py` for a multi-attempt
  success, a permanent failure, and retry-cap exhaustion.
- **Still open, deliberately out of scope for this fix**: a real
  payment-gateway or messaging integration (Phase 15+); a case-level
  re-diagnosis loop after a fully failed action (no such loop exists in
  the state machine, so `app.decision.service._RETRY_COUNT_PENDING_PHASE_6`
  remains `0` — a distinct, decision-level retry count from the
  within-action attempt count this fix introduced).
- **Status**: RESOLVED.

### KI-011: Logging configuration silently overridden in two places (Phase 14) — RESOLVED

- **What (original finding, discovered while building Phase 14's
  structured logging)**: two independent places silently discarded
  `app.core.logging.configure_logging()`'s JSON formatter.
  (1) `migrations/env.py` calls `logging.config.fileConfig()` with its
  default `disable_existing_loggers=True`, which disables every logger
  not explicitly listed in `alembic.ini`'s `[loggers]` section (only
  root/sqlalchemy/alembic are) — including every `app.*` logger. Harmless
  in production (Alembic runs as its own short-lived process, separate
  from the uvicorn process), but a real defect in-process:
  `tests/test_migrations.py` runs Alembic directly inside the same
  pytest process as every other test, permanently silencing Phase 14's
  new log lines for the remainder of any full-suite run after it — first
  surfaced as a test that passed alone but failed only inside the
  700+-test full suite. (2) uvicorn installs its own default logging
  configuration during server startup, which runs *after* `app.main` is
  imported but overrides whatever was configured at import time —
  meaning the production container kept emitting uvicorn's plain-text
  access log instead of this phase's JSON formatter, confirmed live via
  `docker compose logs backend` before the fix.
- **Fix (Phase 14, 2026-09-03)**: `migrations/env.py` now passes
  `disable_existing_loggers=False` to `fileConfig()`.
  `app/main.py`'s FastAPI `lifespan` startup handler reapplies
  `configure_logging()` (idempotent, safe to call twice) after uvicorn's
  own setup runs, so this app's formatter wins last. Reverified: 719/719
  backend tests green in the full suite, and a rebuilt Docker container's
  logs show real JSON request/domain log lines (confirmed via
  `docker compose logs backend`) where before the fix only uvicorn's
  own plain-text access log appeared.
- **Status**: RESOLVED.

### KI-009: Provider fallback-to-mock is observable only after the call completes (Phase 4.1) — RESOLVED

- **What (original finding)**: `app/ai/providers/factory.py::get_reasoning_model`
  silently substitutes `MockProvider` when `REASONING_PROVIDER` names a
  real provider (`qwen`/`nemotron`) but that provider's base URL is unset
  — there was no startup error, warning log, or distinct response signal
  at the moment of substitution, only the persisted diagnosis's
  `model_name` field after the fact.
- **Fix (Phase 10, 2026-09-02)**: `app/ai/providers/factory.py::select_reasoning_model`
  performs the identical resolution logic (never diverges from
  `get_reasoning_model` — both always agree on which provider a plain
  call would get) but returns a `ProviderSelection`
  (`requested_provider`/`resolved_provider`/`substituted`/
  `substitution_reason`) and logs a structured warning at the moment a
  substitution happens. `GET /ai/providers` exposes it live, and
  `scripts/benchmark_diagnosis.py --compare` surfaces the same
  requested-vs-actual distinction in its offline comparison table.
  Additionally, `Diagnosis.router_escalated` /
  `Diagnosis.router_escalation_reason` (migration `9a3e7b5c1d24`) durably
  record a *runtime* substitution (the configured provider was
  transport-unreachable, not merely unconfigured) on the diagnosis row
  itself — the exact "response header, so the substitution is visible
  without a follow-up query" style fix this entry's original resolution
  plan proposed. `get_reasoning_model`/`resolved_provider_name` (the
  frozen Phase 4 contract) are unchanged.
- **Status**: RESOLVED.

### KI-008: Intermittent failure in concurrent-identical-ingestion race test (Phase 4.1) — RESOLVED

- **What (original finding)**: `tests/test_concurrency.py::test_concurrent_identical_ingestion_creates_one_payment`
  fires two identical `POST /events` requests concurrently and expects
  both callers to see `201` (one fresh insert, one idempotent duplicate
  hit). It failed once (`{201, 409}` instead of `{201, 201}`) across 7
  full backend-suite runs in an earlier session. At that point it had not
  been reproduced running alone, run 5x back-to-back alone, or in a
  dedicated 15-iteration stress harness outside pytest, and was recorded
  as UNVERIFIED per Rule 7 rather than silently ignored.
- **Follow-up investigation**: a dedicated forensic pass (read-only, no
  code changes) found the true failure rate was much higher than first
  measured — 40% (12/30) across full-suite runs, 27–33% standalone — and
  that it reproduces for a single isolated test invocation via pytest,
  contradicting the original "full-suite only" theory. A tracing plugin
  (loaded via `pytest -p`, monkeypatching in memory only) captured the
  exact failing interleaving.
- **Root cause (confirmed)**: `ingest_payment_event`
  (`app/services/ingestion.py`) contained a standalone, unprotected
  pre-check between the idempotency-key lookup and the protected
  insert/`except IntegrityError` block:
  ```python
  existing_payment = await session.scalar(
      select(Payment).where(Payment.external_reference == event_in.payment.external_reference)
  )
  if existing_payment is not None:
      raise PaymentReferenceConflictError(event_in.payment.external_reference)
  ```
  This was a time-of-check-to-time-of-use race: when two requests sharing
  the *same* idempotency key raced, the slower request's copy of this
  check could run after the faster request's full transaction had already
  committed, see the now-existing `Payment` row by `external_reference`,
  and unconditionally treat it as a genuine conflict from a *different*
  idempotency key — instead of recognising it as its own duplicate. The
  captured trace showed the exception firing with no `_get_or_create_customer`
  call ever made for the losing request, proving the failure came from
  this pre-check, not from the `except IntegrityError` recovery block. The
  investigation explicitly confirmed the `except IntegrityError`
  rollback → recheck-by-idempotency-key pattern itself was **not** the
  defect (verified separately via `open_case`, which uses that pattern
  alone with no secondary pre-check, and never failed in 30 runs).
- **Fix**: removed the standalone pre-check entirely. The
  `external_reference` uniqueness decision is now made exclusively by the
  database constraint plus the existing `except IntegrityError` recheck,
  which was already correct and constraint-agnostic. No behavior changed
  for any non-racing request; the only removed step was a redundant read
  that was also the race window.
- **Regression coverage added** (`tests/test_concurrency.py`):
  KI008-01 (existing test, tightened intent via docstring) — concurrent
  identical key+reference must both succeed; KI008-02
  (`test_concurrent_same_reference_different_keys_is_a_genuine_conflict`,
  tightened from `codes in ([201,201],[201,409])` to the only
  architecturally-possible outcome `codes == [201, 409]`) — different keys,
  same reference, must still conflict; KI008-03
  (`test_sequential_replay_same_key_and_reference_is_idempotent`) —
  sequential replay unaffected; KI008-04
  (`test_concurrent_identical_ingestion_stress`) — the same race repeated
  25 times in one test run, as a standing regression guard.
- **Verification**: post-fix, `test_concurrent_identical_ingestion_creates_one_payment`
  passed 60/60 standalone runs (vs. 27–40% failure pre-fix under the same
  conditions), the full `test_concurrency.py` file passed 20/20 runs, and
  the full backend suite (136 tests, now +2 from the new KI008-03/04 tests)
  passed 30/30 full runs with zero failures. `ruff check`, `ruff format
  --check`, and `mypy app` all clean. Phase 1 (`test_ingestion.py`,
  `test_ingestion_amounts.py`), Phase 2 (`test_risk_scoring.py`,
  `test_risk_api.py`), Phase 3 (`test_recovery_state_machine.py`,
  `test_recovery_api.py`, `test_recovery_preconditions.py`,
  `test_recovery_safety_contracts.py`), and Phase 4 (`test_ai_diagnosis.py`,
  `test_ai_providers.py`, `test_diagnosis_api.py`, `test_ai_context_builder.py`,
  `test_ai_failure_modes.py`, `test_ai_prompt_injection.py`) suites all
  re-run and green.
- **Status**: RESOLVED.

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
- **Phase 5 dependency (added 2026-08-30)**: Phase 5's decision policy
  engine has no high-value escalation rule for exactly this reason — a
  "high-value" threshold needs a currency-normalized amount, which does
  not safely exist until this issue is resolved (see ADR-006 and the
  `xfail`ed `test_contract_high_value_escalates_to_manual_review` in
  `backend/tests/test_recovery_safety_contracts.py`). Resolving KI-006
  is now a precondition for that contract, not only for `revenue_at_risk`.
  Deliberately not solved now -- no exchange-rate source exists yet and
  guessing one would be premature (Section 44/45 discipline).
- **Status**: Documented limitation, not silently hidden. Not a bypass of
  a requirement -- Phase 2 has no multi-currency requirement.
- **Phase 8 dependency (added 2026-09-02)**: `app.measurement` (the
  revenue-measurement layer) inherits the same discipline deliberately --
  `GET /measurement/report` never sums across currencies (every field is
  a per-currency list: `eligible_at_risk`, `observed_recovered`,
  `observed_not_recovered`, `unresolved`, `recovered_by_strategy`,
  `recovered_by_disposition`). Resolving KI-006 remains a precondition
  for any future single-number cross-currency total in this report, the
  same way it already is for the Phase 5 high-value contract.

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
