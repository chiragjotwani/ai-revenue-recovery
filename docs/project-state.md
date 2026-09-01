# Project State

## Current Phase

Phase 5 — Recovery Decision & Policy Engine: implementation and
verification complete (5A–5I); **awaiting owner review for Phase 5
freeze** (not yet frozen). Phase 4.1 (Stabilization) is complete. Phases
0–2 remain frozen (`docs/phase-0-2-freeze.md`).

Owner decisions recorded:
- 2026-08-27: Phase 3 follows the frozen contract (Recovery Case
  Management state machine), not the ML-training interpretation. AI stays
  inference-only (ADR-003).
- 2026-08-28: Phase 4 diagnosis taxonomy is two layers (specific `outcome`
  + derived `disposition` — ADR-005). Qwen/Nemotron providers built as
  real config-gated OpenAI-compatible HTTP clients; a 6 GB-friendly local
  model path (Ollama `qwen3:4b`) is documented. GPU/model-selection
  decision (KI-002) still open — Phase 4 runs on the `mock` provider.
- 2026-08-30: Phase 5 architecture approved with 15 issues resolved (see
  the Phase 5 Architecture Revision report); no high-value/confidence
  threshold introduced (KI-006 unresolved, ADR-006); decision identity is
  exactly `(case_id, diagnosis_id)`.
- 2026-08-30/31: Phase 5B Section-37 contract split per owner decision
  (evidence-based escalation implemented and passing; high-value
  escalation stays `xfail`, deferred pending KI-006).

## Current Stage

N/A — Phase 5 (5A–5I) closed; Phase 6 not started.

## Completed Phases

- Phase 0 — Engineering Foundation (frozen)
- Phase 1 — Data Foundation (frozen)
- Phase 2 — Revenue Risk Detection (frozen)
- Phase 3 — Recovery Case Management
- Phase 4 — AI Context & Diagnosis
- Phase 4.1 — Stabilization, AI Validation & Product Foundation
- Phase 5 — Recovery Decision & Policy Engine (5A–5I; awaiting owner
  review for freeze)

## Completed Stages (Phase 5)

- **5A — Decision Domain & Contracts** (`app/decision/schema.py`):
  `Recoverability`, `DecisionStatus`, `DecisionRationaleEntry`,
  `DecisionIdentity`, `DecisionResult` (domain type). Reuses
  `RecoveryStrategy` from Phase 4; no confidence threshold, no monetary
  field, no free-text reasoning field.
- **5B — Deterministic Policy Engine** (`app/decision/policy.py`): pure
  `evaluate(PolicyInput) -> PolicyOutcome`, 6 ordered rules
  (already-paid, fraud, insufficient-evidence, retry-cap,
  customer-action-compatibility, safe default). 390+ tests. Section-37
  contracts split per owner decision (ADR-006): evidence-based escalation
  is a real, passing assertion; high-value escalation remains `xfail`,
  deferred to KI-006.
- **5C — Decision Service** (`app/decision/service.py`,
  `app/models/decision.py`, migration `95a41f6c2e7e`): orchestration,
  persistence, and idempotency for `decide_case`. A concurrency defect
  (`sqlalchemy.exc.MissingGreenlet` under 20-way concurrent identical
  decide requests) was root-caused through three escalating
  investigations (pool/pre-ping hypothesis → rejected; pre-warm
  hypothesis → rejected; engine-lifecycle/cross-loop-reuse hypothesis →
  a real but separate test-only hazard, also rejected as the root cause)
  down to a single confirmed defect: a plain, unawaited
  `diagnosis.id` attribute read immediately after `session.rollback()`
  (SQLAlchemy expires ORM attributes on rollback regardless of
  `expire_on_commit`), an expired-attribute access forbidden under
  asyncio SQLAlchemy. Fixed by capturing `diagnosis_id` before the
  rollback path. Verified: 100% reproducible in isolation before the fix
  (20/20), 0/20 after; 30×20-way and 10×50-way stress runs clean against
  the real, unmodified pool configuration; the concurrency test's
  `xfail` marker was removed (now a genuine passing assertion).
- **5D — Precondition Wiring** (`app/recovery/preconditions.py`):
  checkers for `DIAGNOSED → DECISION_PENDING` (a `DecisionResult` exists
  for the case's current diagnosis) and `DECISION_PENDING →
  ACTION_SCHEDULED` (that decision is `approved`). `decide_case`'s own
  transition now runs with `enforce_preconditions=True`.
- **5E — Persistence Reconciliation**: verification only — 5C's
  `DecisionResult` model/migration/unique-constraint/indexes/provenance
  were already complete and correct; nothing rewritten. `alembic check`
  clean, single migration head, upgrade/downgrade roundtrip clean.
- **5F — API** (`app/api/recovery.py`, `app/schemas/recovery.py`):
  `POST /recovery/cases/{id}/decide` (idempotent, `200` for
  approved/escalated/rejected alike — a policy outcome is never an HTTP
  error; `404`/`409`/`422`/`500` for genuine failure modes) and
  `GET /recovery/cases/{id}` enriched with the case's `decision`.
- **5G — Frontend**: the case-detail page's "4 · Decision" panel renders
  recoverability, candidate/approved strategy (with a downgrade
  indicator), decision status (approved/escalated/rejected/superseded,
  none treated as an error), structured rationale, `scheduled_not_before`,
  engine version, and decision time, sourced from the real API. A
  `useActionState`-backed "Decide" form (only shown for a `diagnosed`
  case) invokes the real `POST /decide` and handles 404/409/422/5xx
  inline without crashing the page. Verified against the real backend via
  a live local dev server and real seeded data (approved and escalated
  cases) — browser-extension automation was unavailable in this session,
  so verification used direct HTTP fetches of the real server-rendered
  HTML instead of an interactive browser; this is recorded as a
  limitation, not claimed as full visual/responsive browser verification.
- **5H — Evaluation Harness** (`evaluation/decision_cases.json`,
  `scripts/benchmark_decision_policy.py`,
  `tests/test_decision_evaluation_harness.py`): 16 hand-authored golden
  cases covering all 8 documented policy rules, safety invariants (fraud
  never retries, already-paid always `no_action`, sparse/conflicting
  evidence never auto-recovers, retry cap respected, no monetary/
  confidence field, `PolicyInput` forbids extra fields), and determinism
  (20x re-evaluation, byte-identical). Explicitly does not measure, and
  is not a substitute for, real-world recovered revenue (KI-007 applies
  with equal force to this harness).
- **5I — Documentation Reconciliation**: this update.

## Completed Stages (Phase 4.1)

Full detail in the Phase 4.1 implementation report (session record). Summary:

- **Correctness (Workstream A)**: fixed the migration downgrade leaving
  the `payment_status` enum orphaned (roundtrip now clean, repeatable —
  `tests/test_migrations.py`); oversized/sub-cent monetary input now
  rejected at the API with 422 instead of reaching Postgres
  (`tests/test_ingestion_amounts.py`); invalid recovery-case UUIDs now
  render a distinct 404 instead of "backend unreachable" (BUG-004);
  `GET /health/ready` added as a truthful readiness probe (Postgres only —
  Redis is declared but not used by any code path yet, so it is
  deliberately not probed).
- **AI validation (Workstream B)**: traced provider abstraction end to end
  (diagnosis service has no provider-specific logic); a local Ollama
  endpoint was verified reachable and real-model integration tests were
  added as conditional/opt-in (`tests/test_ai_real_model.py`) — see the
  AI Reality Check in the Phase 4.1 report for whether they actually ran
  in a given verification pass; `diagnosis_prompt_v2` adds an explicit
  four-layer prompt-injection boundary
  (system/context/untrusted-data/schema), tested in
  `tests/test_ai_prompt_injection.py`; failure-mode coverage added for
  timeout, malformed JSON, invalid schema, unsafe output text
  (`tests/test_ai_failure_modes.py`); confidence is documented as
  model-reported, not a calibrated probability.
- **Recovery safety contracts (Workstream C)**: `app/recovery/preconditions.py`
  declares the artifact each forward state transition depends on
  (diagnosis, policy decision, action, observed payment event) and is
  enforced in `transition_case`; Section 37 safety cases (forbidden
  action, duplicate action, already-recovered, high-value escalation)
  pinned as executable specs in `tests/test_recovery_safety_contracts.py`;
  action/execution/idempotency-key identity contract written down for the
  Phase 6 executor (`docs/recovery/action-idempotency.md`) — no executor
  exists yet.
- **Frontend (Workstream D)**: removed the "Phase 2" implementation-phase
  language from the customer-facing UI; built a persistent app shell (nav,
  live ticker) and a "Trading Floor Terminal" design system
  (`DESIGN.md`); redesigned the dashboard around revenue-at-risk /
  recoverable opportunity / active cases, explicitly labeling
  not-yet-computable metrics "Not available yet" rather than fabricating
  them; case detail page walks PAYMENT → RISK → AI DIAGNOSIS →
  RECOMMENDATION → RECOVERY STATUS → OUTCOME with mock/real provenance
  and an explicit "advisory only" label on the recommendation; added
  `error.tsx` / `loading.tsx` / `not-found.tsx` and a distinct
  backend-unavailable banner.
- **Testing (Workstream E)**: added Vitest + React Testing Library
  (`frontend/vitest.config.mts`), 24 frontend tests across 4 files;
  concurrency tests for the `IntegrityError` recovery branches in
  ingestion and case-open (`tests/test_concurrency.py`); reclassified the
  mock benchmark as pipeline/schema validation only, not model accuracy
  (KI-007, pre-existing, reaffirmed).

## Completed Stages (Phase 4)

- Diagnosis schema (`backend/app/ai/schema.py`): `DiagnosisOutcome` (10
  specific causes incl. `unknown`), `DiagnosisDisposition` (4 routing
  categories, **derived from outcome by code**, not by the model),
  `RecoveryStrategy` (advisory), `ModelDiagnosisJSON` (the strict provider
  contract) and `Diagnosis` (validated + enriched). ADR-005.
- `RecoveryContextBuilder` (`backend/app/ai/context_builder.py`, Section
  49): bounded customer / payment / failure / capped-history / policies
  summary from Postgres only, plus derived `evidence_sufficiency` and
  `signals_conflict` signals. Never raw rows.
- Prompt versioning (`backend/app/ai/prompts.py`): `diagnosis_prompt_v1`,
  stored with every diagnosis (Section 50).
- `ReasoningModel` abstraction (`backend/app/ai/providers/`, Section 7):
  ABC + `MockProvider` (deterministic default; no model/network) +
  `QwenProvider` / `NemotronProvider` as real config-gated
  OpenAI-compatible HTTP clients + a factory that falls back to `mock`
  when a provider's base URL is unset.
- Structured-output validation + safeguards (`backend/app/ai/diagnosis.py`,
  Section 37): extract JSON → schema-validate → retry once → raise
  `DiagnosisValidationError` (never proceed); sparse context downgrades an
  over-confident answer to `unknown`; conflicting signals cap confidence.
- `diagnoses` table (`backend/app/models/diagnosis.py`, migration
  `2977e606c234`) storing outcome/disposition/confidence/reasoning/
  strategy + `model_name` / `model_version` / `prompt_version` /
  `schema_version` / `latency_ms` (Section 51). Strings not PG enums —
  ADR-005. Upgrade/downgrade roundtrip + `alembic check` clean.
- `diagnose_case` service + `POST /recovery/cases/{id}/diagnose`: build
  context → run provider → validate → persist → advance
  `detected → diagnosing → diagnosed`. `404` unknown / `409` wrong state /
  `502` unusable model output (case left in `diagnosing`, retryable,
  nothing persisted). `GET /recovery/cases/{id}` gains a `diagnosis` field.
  The AI path has no write access to payments and no action executor
  (ADR-003).
- Evaluation set (`backend/evaluation/diagnosis_cases.json`, 72 synthetic
  labelled cases; deterministic generator) + benchmark runner
  (`backend/scripts/benchmark_diagnosis.py`): outcome accuracy, schema
  compliance, hallucination rate, confidence-band adherence, latency,
  throughput (Section 52). KI-007 records that the numbers are agreement
  with synthetic labels, not real-world accuracy.
- Frontend: diagnosis panel on `/recovery/[id]`.
- ADR-005 added. KI-002 updated with the 6 GB GPU finding; KI-007 added.
- Tests: `test_ai_diagnosis.py` (22), `test_ai_providers.py` (11),
  `test_ai_context_builder.py` (4), `test_diagnosis_api.py` (5) — 42 new,
  **91 total passing**.

## Completed Stages (Phase 3)

- Models (`backend/app/models/recovery.py`): `RecoveryCase` (one per
  payment — `payment_id` unique), `RecoveryCaseTransition` (append-only
  history), `RecoveryCaseState` enum (full 10-state lifecycle from
  Section 16, defined now so later phases don't need enum migrations)
- Migration `e9bad135ac97`: `recovery_cases` + `recovery_case_transitions`
  + the `recovery_case_state` Postgres enum (created once, referenced with
  `create_type=False`); upgrade/downgrade roundtrip verified;
  `alembic check` shows no drift
- State machine (`backend/app/recovery/state_machine.py`): single
  `LEGAL_TRANSITIONS` map + `TERMINAL_STATES` + `INITIAL_STATE`; linear
  path plus terminal edges, no retry back-edge yet
- Transition service (`backend/app/recovery/service.py`): `open_case`
  (idempotent on payment, returns `(case, created)`, handles the
  concurrent-open race), `transition_case` (validates against the state
  machine, writes the history row + state change in one transaction,
  raises `IllegalStateTransitionError`, sets `closed_at` on terminal),
  plus `get_case` / `get_case_transitions` / `list_cases`
- API (`backend/app/api/recovery.py`, `docs/api/recovery.md`):
  `POST /recovery/cases` (201 new / 200 existing / 404 unknown payment /
  409 not-failed), `GET /recovery/cases?state=`,
  `GET /recovery/cases/{id}` (with ordered history),
  `POST /recovery/cases/{id}/transitions` (200 / 404 / 409 illegal /
  422 bad state)
- Frontend: `/recovery` (case list, state badges) and `/recovery/[id]`
  (case detail + transition timeline), SSR, same style as `/risk`; linked
  from the risk dashboard
- Tests: `test_recovery_state_machine.py` (17 pure tests: every state
  mapped, terminals have no exits, no self-loops, full happy path legal,
  8 parametrized illegal transitions raise) and `test_recovery_api.py`
  (10 integration tests vs. real Postgres: open/idempotent-open/404/409,
  legal & illegal transitions via API, terminal sets `closed_at` and
  freezes, list + state filter, chronological history) — 27 new tests,
  **49 total passing**
- ADR-004 added (explicit recovery state machine + append-only transition
  log)
- Full verification: 49/49 pytest, ruff/format/mypy strict clean, frontend
  eslint/tsc/build clean, `alembic upgrade`/`downgrade`/`check` clean,
  verified end-to-end in a from-clean Docker rebuild

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
- Phase 3 non-blocking notes (not defects): the concurrent-open race
  branch in `open_case` and the `seed_synthetic_data.py` script still have
  no dedicated automated test (carried over from Phase 1); no frontend
  test runner exists yet, so `/recovery` pages are verified by rendering
  SSR HTML.
- Phase 4: KI-007 added (synthetic evaluation set; benchmark accuracy is
  label-agreement, not real-world). KI-002 updated (6 GB GPU can't host
  the 30B candidates; `mock` provider is the Phase 4 default).
- Phase 4.1: KI-008 added and RESOLVED (concurrent-ingestion TOCTOU race,
  see below); KI-009 added, open (provider fallback-to-mock is only
  observable after the call completes).
- Phase 5: KI-006 remains open and unresolved by design (no high-value/
  cross-currency threshold introduced — ADR-006; the corresponding
  Section-37 contract stays `xfail`). KI-007's synthetic-data caveat
  extends to the Phase 5H decision-policy evaluation harness as well
  (golden policy cases, not a claim of real-world validity).

## Architecture Decisions

- ADR-001: PostgreSQL as source of truth
- ADR-002: Modular monolith
- ADR-003: LLM cannot directly execute financial actions
- ADR-004: Explicit recovery state machine with an append-only transition log
- ADR-005: Diagnosis output — two layers, derived disposition, everything versioned
- ADR-006: Model confidence is not a deterministic policy threshold (Phase 5)

## Last Successful Verification

2026-08-31 Phase 5 (5A–5I) complete (this session). Backend: full suite
**590 passed, 8 skipped (pre-existing, unrelated), 2 xfailed
(`test_contract_high_value_escalates_to_manual_review` → KI-006,
`test_contract_duplicate_action_is_idempotent` → Phase 6), 0 failed**,
stable across 4 consecutive full-suite runs; `ruff check`, `ruff format
--check`, `mypy` (no new-category errors in any 5A–5I implementation
file) all clean. Decision-service concurrency: 30×20-way and 10×50-way
stress runs against the real, unmodified `pool_pre_ping=True` engine, 0
failures; the same invariant re-verified end-to-end through the real
`POST /recovery/cases/{id}/decide` HTTP endpoint (20 concurrent requests
→ 1 `DecisionResult`, 1 transition). A genuine test-infrastructure defect
was found and fixed during this work: `app.db.session.engine`, a
module-level singleton, was being reused across pytest's per-test event
loops without disposal, an unsafe pattern SQLAlchemy's own documentation
warns against; fixing it (dispose the engine at the start of every test)
resolved an intermittent full-suite-only concurrency-test failure that
did not reproduce in isolation. Frontend: `vitest` 41/41, `eslint`
clean, `tsc --noEmit` clean, `next build` clean. Evaluation:
`scripts/benchmark_decision_policy.py` 16/16 golden cases pass, safety
invariants pass, determinism confirmed (20x re-evaluation, identical).
Real end-to-end verification: seeded two real cases (one
`insufficient_funds`, one `fraud_suspected`) through the live API and a
locally-run frontend dev server, triggered real decisions via
`POST /decide`, and confirmed the real server-rendered HTML showed the
correct approved/escalated states, strategies, and rationale — the
Chrome browser-automation extension was unavailable in this session, so
this HTTP-level verification is the strongest evidence obtained; true
interactive/visual/responsive-width browser verification was not
performed and remains outstanding for a future session before Phase 5
freeze if the owner wants it. No Phase 6/7/8 code was introduced.

2026-08-30 KI-008 fix (this session). Root-caused and fixed the intermittent
concurrent-ingestion race (see `docs/known-issues.md` KI-008, now RESOLVED):
removed a time-of-check-to-time-of-use pre-check in
`ingest_payment_event` that could misclassify a concurrent caller's own
idempotency-key duplicate as a genuine cross-key conflict. Post-fix:
`test_concurrent_identical_ingestion_creates_one_payment` 60/60 standalone
runs clean (was 27–40% failure), `test_concurrency.py` 20/20 full-file
runs clean, full backend suite (136 tests, +2 new KI-008 regression tests)
30/30 full runs clean, `ruff check`/`ruff format --check`/`mypy app` all
clean. Phase 1/2/3/4 relevant suites re-run and green. No frontend, AI,
recovery-decisioning, or documentation-beyond-KI-008 changes made in this
pass — scope was strictly the ingestion race fix and its regression
coverage.

2026-08-29 Phase 4.1 gate (this session). Backend: **pytest 133 passed, 8
skipped, 4 xfailed** against dockerized Postgres/Redis (one concurrency
test, `test_concurrent_identical_ingestion_creates_one_payment`, failed
once intermittently across 7 full-suite runs — reproduces only inside the
full suite, not in isolation or a 15-iteration stress harness; documented
as an open, unresolved intermittent finding, not silently dropped — see
KI-008), ruff check, ruff format --check, mypy strict (47 files) — all
clean. Frontend: eslint, `tsc --noEmit`, vitest (24/24), `next build` — all
clean. Docker stack rebuilt from the current working tree
(`docker compose up -d --build backend frontend`) and browser-verified at
390px/768px/1280px: dashboard, risk queue, recovery list, case-open, AI
diagnosis panel (mock provenance visible), invalid-case 404, and
backend-unavailable banner all behaved as designed; no console errors
observed. Canonical scenario (historical success → failed payment → risk
detected → case opened → context built → diagnosed →
`insufficient_funds`/`retriable_transient`/retry+6h, advisory only) run
manually end-to-end via the API and confirmed.

2026-08-28 Phase 4 gate. Backend: **pytest 91/91**, ruff check, ruff
format --check, mypy strict (46 files), `alembic upgrade head` from clean
(3 migrations), `alembic check` (no drift), `alembic downgrade -1`/
`upgrade` roundtrip on the diagnoses migration — all clean. Frontend:
eslint, `next typegen` from clean, `tsc --noEmit`, `next build` (routes
`/`, `/risk`, `/recovery`, `/recovery/[id]`) — all clean.
`benchmark_diagnosis.py --provider mock` runs over 72 eval cases (all
metrics 1.0 by construction — KI-007). Full stack rebuilt from clean
(`docker compose down -v && up --build`): all 3 migrations auto-applied on
backend container start; canonical scenario seeded; case opened, then
`POST /recovery/cases/{id}/diagnose` → `200`
`insufficient_funds`/`retriable_transient`/conf 0.9/**retry +6h** (matches
Section 38), case advanced `detected → diagnosing → diagnosed`, re-diagnose
→ `409`; `/recovery/[id]` rendered the diagnosis panel via SSR across the
container network. Phase 0–3 regression still green within the 91.

The Phase 0–2 freeze gate detail remains in `docs/phase-0-2-freeze.md`.

## Last Git Commit

`phase-4: implement AI recovery diagnosis` — see `git log`.
(Preceded by `phase-3: …` and `freeze: phases 0-2 verified`.)

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is raised to the project owner for
approval before proceeding.
