# Project State

## Current Phase

Phase 17 — Advanced Autonomous Recovery: implementation and verification
complete (2026-09-05), scoped by explicit owner decision to
human-in-the-loop manual review resolution only (the one item on the
master plan's Phase 17 wishlist this project could honestly build).
Phase 16 (Real Payment Integration) is **frozen**. Phases 4.1, 5, 6, 7,
8, 9, 10, 11, 12, 13, 14, and 15 are also frozen. Phases 0–2 remain
frozen (`docs/phase-0-2-freeze.md`). This completes the full roadmap
through Phase 17.

## Phase 15 (2026-09-05)

Owner instruction: verify Phase 14 first, then continue through the
remaining roadmap phases (15 Security & Fintech Hardening, 16 Real
Payment Integration, 17 Advanced Autonomous Recovery). Phase 14
re-verified clean before starting (758 backend tests passing at the
time -- 733 pre-Phase-15 plus the 25 new Phase 15 tests once added;
ruff/mypy/eslint/tsc all clean; frontend 64/64 Vitest tests passing).
Scope for Phase 15 itself was confirmed via AskUserQuestion given the
real, non-trivial risk of adding auth/authz/rate-limiting to every
endpoint including money-adjacent mutations: the "core hardening set"
was chosen over a narrower auth-only slice or an audit-integrity-only
alternative.

Before this phase, `app/main.py` registered every router with zero
authentication, authorization, rate limiting, or CORS policy --
confirmed by grep, not assumed. What was built:

- **`app/core/auth.py`**: `Role` (`operator`/`readonly`),
  `parse_api_keys` (`"key:role,key:role"` -> `dict[str, Role]`,
  `ValueError` on a malformed entry), `require_role(minimum)` (a FastAPI
  dependency factory; `Security(APIKeyHeader(...))` reads `X-API-Key`,
  constant-time-per-candidate comparison via `hmac.compare_digest` to
  avoid a timing side channel). Missing/unknown key -> `401`; known key,
  insufficient role -> `403`.
- **Router wiring**: every `POST` under `/recovery/cases/...`,
  `POST /events`, and `POST /analytics/warehouse/rebuild` requires
  `Role.OPERATOR`; every `GET` (recovery, risk, measurement, analytics,
  warehouse, ai-models) requires at least `Role.READONLY`.
  `/health`, `/health/ready`, `/metrics` stay unauthenticated (infra
  probes/scrapers, exempted the same way the rate limiter exempts them).
- **`app/core/rate_limit.py::RateLimitMiddleware`**: Redis-backed fixed
  window (`INCR`+`EXPIRE`, one round trip, `redis` was already a
  declared dependency and unused until now), keyed by API key or client
  IP, `429` with `Retry-After` over budget. Fails OPEN on a Redis error
  (logged, not swallowed) -- an infra dependency that is not the system
  of record must never block request-serving (ADR-007's own principle,
  applied here).
- **`app/core/security_headers.py::SecurityHeadersMiddleware`**:
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` always;
  `Strict-Transport-Security` in production only (meaningless/wrong
  outside real TLS, gated like `docs_url` already is).
- **CORS**: off by default (`Settings.cors_allowed_origins` empty --
  this bundled frontend calls the backend server-to-server, never from a
  browser origin, KI-001); added via Starlette's `CORSMiddleware` only
  when configured.
- **Production startup guard**: `create_app()` raises `RuntimeError` if
  `Settings.is_production` and no API keys are configured, rather than
  silently serving every endpoint open. Development/test are exempt --
  an empty key map there means every request gets a real `401`, which is
  exactly what the test suite's dedicated auth tests exercise; there is
  no separate "auth disabled" code path.
- **Frontend**: `frontend/src/lib/backend-auth.ts` attaches a single
  operator-role `BACKEND_API_KEY` (server-only env var, never
  `NEXT_PUBLIC_*`) to every one of this app's five server-to-server
  fetch call sites (`lib/api.ts`, `recovery/[id]/decide-action.ts`,
  `action-action.ts`, `outcome-action.ts`, `risk/actions.ts`).
- **Existing test suite migration**: `tests/conftest.py`'s shared
  `client` fixture now attaches a fixed test operator API key by
  default (`API_KEYS_RAW` env var set via `os.environ.setdefault`,
  mirroring KI-010's own pattern) so the pre-existing 733 tests did not
  need individual changes; the one test file that built its own
  `AsyncClient` directly (`tests/test_concurrency.py`) was updated to do
  the same. `RATE_LIMIT_REQUESTS_PER_MINUTE` is raised in tests (the
  full suite legitimately exceeds a real per-minute budget against one
  key) rather than the limiter being disabled outright, so
  `tests/test_rate_limit.py` can still exercise a real, low limit by
  overriding `app.core.rate_limit.get_settings` per-test.
- **`docker-compose.yml` / `.env.example`**: `API_KEYS_RAW`,
  `CORS_ALLOWED_ORIGINS_RAW`, `RATE_LIMIT_REQUESTS_PER_MINUTE` wired to
  the backend service; `BACKEND_API_KEY` wired to the frontend service.
  Verified live end-to-end via a real `docker compose up --build`: an
  unauthenticated `GET /recovery/cases` returns `401`, the operator key
  returns `200`, the readonly key gets `403` on a mutating endpoint,
  `/health`/`/metrics` remain open with no key, security headers are
  present on a live response, and the frontend's own `/risk` page
  renders real data (not a "backend unavailable" fallback), proving its
  `BACKEND_API_KEY` round-trip actually works end-to-end.

Verified: backend full suite 758/758 passing (0 failures; 8 skipped, 1
`xfail`, both pre-existing and unrelated) including 25 new Phase 15
tests (`tests/test_auth.py`, `tests/test_rate_limit.py`,
`tests/test_security_headers.py`); `ruff check` / `ruff format --check`
/ `mypy app` all clean; frontend `eslint` / `tsc --noEmit` / Vitest
(64/64) all clean; live Docker verification as above.

Explicitly NOT built in this phase, and why: end-user login/session/
OAuth (this is a service-to-service backend with no end-user-facing
login flow anywhere in this codebase -- API-key auth is the correct
shape for its actual callers, not a placeholder for a fuller scheme);
tamper-evidence/hash-chaining of the existing audit trail (a real,
separate hardening concern, deliberately deferred rather than bundled
in without its own scoping pass); a secrets-manager integration (API
keys are plain environment variables, the same mechanism every other
credential in this system -- database password, Kafka config -- already
uses; introducing a different mechanism for only this one credential
class would be inconsistent, not more secure, without a real secrets
backend already present in this deployment).

## Buildathon finalization (2026-09-04)

After Phase 6's completion (commit `618af45`), priority shifted from
architectural completeness to a stable, convincing, buildathon-ready
product. Every remaining gap was triaged rather than closed mechanically
-- see the finalization session's own report for the full A-E
classification. What was built:

- **KI-010 resolved**: `tests/conftest.py` now defaults `DATABASE_URL` to
  a dedicated test database automatically (`os.environ.setdefault`,
  never overriding an explicit value) -- a bare `pytest` invocation can
  no longer silently truncate the shared dev database. Regression-tested
  in a genuinely clean subprocess (`test_safe_test_database_default.py`).
- **Baseline-vs-AI comparison** (`app/measurement/baseline.py`,
  `GET /measurement/baseline-comparison`): a "blind retry" baseline
  policy's outcome, computed by calling the SAME deterministic simulated
  provider the real pipeline uses (pure, no side effects, nothing
  persisted), compared against the real, OBSERVED AI-gated recovery rate
  over the same eligible case population. Explicitly NOT a randomized
  control/treatment experiment and NOT a causal/incremental-lift claim --
  see `BaselineComparisonReport.methodology`. Surfaced on the dashboard.
- **Deterministic demo population** (`scripts/seed_demo_population.py`):
  28 customers / ~148 events across six named behavioral profiles (not
  random noise), driven through the real recovery pipeline via ordinary
  HTTP calls -- no shortcuts, no direct DB writes. Kept entirely separate
  from `scripts/seed_synthetic_data.py`'s single canonical fixture, which
  existing tests/manual QA still depend on unchanged. Deliberately modest
  in scale (not "thousands") -- large enough for meaningful dashboard
  distributions, small enough to seed in well under a minute and stay
  easy to reason about live in a demo.
- **Two new scenario tests** (payment-link and notification simulated
  success) filling out Phase 6's scenario coverage.
- **Explicitly NOT built, and why**: a trained ML recovery-probability
  model (Phase 9) -- the existing empirical-frequency strategy analytics
  were kept and documented as the interim state; training, evaluating,
  and versioning a real model within the remaining time carried more risk
  of a half-finished subsystem than value for a demo, per the session's
  own "quality > checkbox" instruction. A randomized control/treatment
  experiment was also not attempted -- no real counterfactual population
  exists, and fabricating one would violate this project's own
  discipline (KI-006's frozen position). OpenTelemetry, Kubernetes, and
  any Phase 15+ (real payment/messaging integration) work were out of
  scope by explicit instruction.

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
- 2026-09-01: Phase 6 (Action Executor) frozen — no real payment-provider
  integration exists or was invented; `retry`/`contact_customer`/
  `request_payment_method_update` executions are honestly recorded as
  `deferred_no_integration`, never fabricated as completed.
- 2026-09-03: Phase 6 completed — a full repository audit identified this
  as the single most consequential gap against the project's own phase
  specification (the DETECT→ACT→OBSERVE loop was not genuinely closed: no
  action this system took ever caused a recovery). Closed with a
  deterministic, explicitly SIMULATED execution layer
  (`app/decision/providers.py` / `app/decision/executors.py`) reached only
  from the bounded action-executor layer (ADR-003 unaffected). Phase 7 and
  Phase 8 were NOT modified — see KI-012 (resolved) and
  `docs/recovery/action-idempotency.md` for what changed and what remains
  deliberately out of scope (a real payment-gateway integration; a
  case-level re-diagnosis loop).
- 2026-09-01: Phase 7 (Outcome Observation) frozen — reuses the exact
  `already_paid`-style correlation already established in Phase 5/Phase 2,
  no new fuzzy matching; `action_executed` and `recovered` are kept
  structurally distinct.
- 2026-09-02: Phase 8 (Recovered Revenue Measurement) frozen — every
  reported number is OBSERVED evidence (a later successful/failed payment
  exists), never a causal/incremental estimate; no counterfactual/control
  cohort exists in this system, so none is claimed (see
  `app/measurement/schema.py::COUNTERFACTUAL_LIMITATION`). KI-006 remains
  unresolved and is NOT worked around — every monetary aggregate is a
  per-currency list, never a cross-currency sum.
- 2026-09-02: Phase 9 scoped by explicit owner decision (asked mid-session
  via AskUserQuestion, given a genuine architectural tension the master
  plan's own Phase 9 definition creates): the master plan
  (`docs/master-loop-engineering-prompt.md`, Section 28) calls for a
  "historical strategy dataset," "strategy analytics," an "ML recovery
  model," "recovery probability," and "strategy optimization." Only the
  first two are implemented. The ML model/probability/optimization are
  deliberately NOT built — this system has no real-world outcome data at
  any volume that could train or validate a genuine predictive model
  (KI-007), and ADR-006 already forbids exactly this shape of thing
  (confidence/probability driving policy) at the decision layer. Building
  a "model" from a handful of synthetic demo cases would be statistically
  meaningless theater, not a real capability — so it was not built, and
  the gap is disclosed explicitly in every API response
  (`ml_model_status: "not_implemented"` +
  `app/analytics/schema.py::ML_MODEL_LIMITATION`) rather than silently
  dropped or faked.
- 2026-09-02: Phase 10 scoped by explicit owner decision (asked mid-session
  via AskUserQuestion, given the same class of tension Phase 9 hit): the
  master plan (`docs/master-loop-engineering-prompt.md`, Section 29) calls
  for "model router," "confidence routing," "model comparison," "latency
  monitoring," "model evaluation," and "advanced-model escalation."
  Everything except confidence routing is implemented. Escalation
  (`app/ai/providers/router.py::run_diagnosis_with_failover`) is driven
  EXCLUSIVELY by an observable transport failure (`ReasoningModelError`),
  never by the diagnosis's self-reported confidence -- this project has
  twice already documented that confidence is model-reported and
  uncalibrated (Phase 4.1's AI validation stage; ADR-006, which forbids
  exactly this shape of thing at the policy layer) and declined to trust
  it; extending that same discipline here. Config-time provider
  substitution is now explicit and observable (`select_reasoning_model`,
  logged, exposed via `GET /ai/providers`), closing KI-009. Runtime
  escalation is durably recorded on the diagnosis row itself
  (`Diagnosis.router_escalated` / `.router_escalation_reason`, migration
  `9a3e7b5c1d24`) rather than only in a transient log line.
- 2026-09-02: Phase 11 scoped by explicit owner decision (asked
  mid-session via AskUserQuestion, given the same class of tension Phase
  9/10 both hit): the master plan (`docs/master-loop-engineering-prompt.md`,
  Section 30) calls for "embeddings," "historical case retrieval," "vector
  storage," "similarity search," "retrieval context," and "retrieval
  evaluation." All six are implemented, but "embeddings" are DETERMINISTIC
  STRUCTURED-FEATURE VECTORS (`app/retrieval/embedding.py`) -- a one-hot/
  normalized encoding of a case's disposition, outcome, confidence, risk
  features, and payment amount -- never a learned/neural embedding. No
  embedding-model endpoint exists anywhere in this repository (the same
  infrastructure gap KI-002 already documents for the reasoning-model
  layer). "Retrieval context" is scoped to a read-only, operator-facing
  "similar past cases" endpoint/panel -- it deliberately does NOT modify
  Phase 4's frozen diagnosis prompt/context-builder pipeline (no new
  prompt version, no change to the four-layer prompt-injection boundary),
  since historical-case retrieval and similarity search are fully
  realizable without reopening that frozen pipeline. "Retrieval
  evaluation" is a correctness check of the ranking mechanism itself
  (does it rank matching-disposition/outcome cases higher?), never a
  claim about improved diagnosis accuracy (KI-007 applies with equal
  force here, same as Phase 5H/9's own evaluation harnesses).
- 2026-09-02: Phase 12 (Asynchronous Event Architecture) built on top of
  ADR-002's own forward reference ("see Phase 12 for the point at which
  async/event infrastructure is introduced") -- resolving the apparent
  Kafka-vs-modular-monolith tension by construction rather than by
  picking a side: the backend stays one deployable service; Kafka is an
  audit/integration bus only and never a trigger for domain logic (see
  ADR-007). An outbox pattern (`domain_events`, written in the same
  transaction as the state change it describes, via
  `app.recovery.service.open_case`/`transition_case` -- the single
  choke point for every case state change) avoids an unsafe dual-write
  between Postgres and Kafka. Idempotent consumption
  (`processed_events`, unique on `(event_id, consumer_group)`, same
  KI-008 DB-authoritative discipline as every prior phase) plus bounded
  retry and dead-lettering (`dead_letter_events`) mean a redelivered or
  failing event never duplicates a business effect and never retries
  forever. The one wired consumer, `EventAuditProjector`, is
  deliberately read-only (a structured log line, no database write, no
  domain-service call) -- it cannot duplicate a business effect no
  matter how many times it is redelivered, by construction. Verified
  live end-to-end via `docker compose` (not just unit tests): a real
  `POST /recovery/cases` call produced an outbox row, the relay
  (`scripts/event_relay.py`) published it to a real single-node KRaft
  Kafka broker, and the consumer (`scripts/event_consumer.py`) consumed
  and processed it (`outcome=handled attempts=1`), with `published_at`
  and the `processed_events` marker both confirmed via `psql`.

- 2026-09-02: Phase 13 scoped by explicit owner decision (asked
  mid-session via AskUserQuestion, given the same class of tension Phase
  9/10/11 each hit): the master plan calls for an analytical model
  covering "revenue at risk, recovered revenue, natural recovery,
  incremental recovery, recovery attempts, strategy performance, failure
  categories, customer segments, model performance, and experiment
  performance." Everything except "incremental recovery" and "experiment
  performance" is implemented, for the same reason Phase 8 never computed
  a causal recovered-revenue figure: no randomized control group or
  other counterfactual design exists here (`app.warehouse.schema`'s
  `EXPERIMENT_LIMITATION`). "Natural recovery" turned out to have its own
  narrower gap, discovered while building this phase rather than assumed
  upfront: Phase 7's `observe_outcome` (which Phase 8's own revenue
  report depends on via `get_outcome_for_case`) only ever classifies a
  case `recovered` when it has an executed `RecoveryAction` -- an
  escalated/action-less case is definitionally `unresolved` under that
  frozen rule, regardless of what the raw payment evidence shows.
  Classifying such cases from raw evidence anyway (`classify_outcome` can
  do this) would give them a different outcome than Phase 8's own report
  computes for the identical case -- exactly the "redefine Phase 8's
  financial semantics" this phase must not do. So `natural_recovery` is
  disclosed as `not_measurable` (`NATURAL_RECOVERY_LIMITATION`) rather
  than computed from a broadened, Phase-8-inconsistent definition.

- 2026-09-03: Phase 14 (Production Observability) implemented in full --
  no scope tension with any prior phase's frozen semantics, unlike
  Phases 9–13 (structured logging, metrics, and health checks describe
  the system, they do not compute new business facts). One real,
  pre-existing defect was found and fixed while building it: Alembic's
  `fileConfig()` (`migrations/env.py`) defaults to
  `disable_existing_loggers=True`, which silently disabled every `app.*`
  logger not explicitly listed in `alembic.ini` -- harmless in
  production (alembic runs as its own short-lived process, separate from
  the uvicorn process) but a real defect in-process, since
  `tests/test_migrations.py` runs Alembic directly inside the same
  pytest process as every other test, permanently silencing this
  phase's new log lines for the remainder of any full-suite run after it
  (reproduced: passed in isolation, failed only in the full 700+ test
  suite; root-caused, not worked around -- fixed by passing
  `disable_existing_loggers=False`, then reverified 719/719 green). A
  second, smaller defect was also found and fixed: uvicorn installs its
  own default logging configuration during server startup, which runs
  AFTER this module is imported, silently overriding
  `app.core.logging.configure_logging()` if that were only called at
  import time -- fixed by also reapplying it from the FastAPI lifespan
  startup handler (`app/main.py::_lifespan`), which runs last; confirmed
  live in a rebuilt Docker container (structured JSON request/domain log
  lines now actually appear in `docker compose logs backend`, where
  before the fix only uvicorn's own plain-text access log did).

## Phase 16 (2026-09-05)

Scope confirmed via AskUserQuestion before implementation, given the
real risk of a real-money-adjacent integration: "Stripe TEST mode"
(real SDK/API calls, no live money) over a narrower
"pluggable-interface-only, no real call" alternative or skipping the
phase. A second AskUserQuestion then confirmed the exact blast radius:
only the `retry` channel gets a real gateway (`request_payment_method_update`
/ `contact_customer` stay simulated -- no real payment-link or messaging
integration was in scope), config-gated with a simulated fallback, and
`PaymentProvider.attempt` becomes `async`.

What was built:

- **`app/decision/providers_stripe.py`**: `StripePaymentProvider` calls
  the real `https://api.stripe.com/v1/payment_intents` endpoint via
  `httpx.AsyncClient` (matching `app/ai/providers/openai_compatible.py`'s
  existing pattern -- never the synchronous `stripe` SDK, which would
  block this codebase's asyncio event loop; no new PyPI dependency was
  added). `_require_test_key` refuses a live key
  (`StripeConfigurationError`) at construction. Stripe's own published
  TEST-mode payment-method tokens (`pm_card_visa`,
  `pm_card_chargeDeclinedInsufficientFunds`, etc. --
  https://docs.stripe.com/testing) are selected by the same
  `failure_reason` the simulated provider's profile table already keys
  off of, so a demo scenario behaves analogously regardless of which
  provider is active. `select_retry_provider()` resolves
  `StripePaymentProvider` when `Settings.stripe_api_key` is set,
  otherwise the unchanged `SimulatedPaymentProvider`.
- **`PaymentProvider.attempt` is now `async`** (`app/decision/providers.py`):
  a real gateway call is genuine network I/O. `SimulatedPaymentProvider`,
  `RetryExecutor`/`PaymentLinkExecutor`/`NotificationExecutor`
  (`app/decision/executors.py`), `execute_action`
  (`app/decision/actions.py`), and the Phase 8 baseline comparison's
  `_baseline_recovers` (`app/measurement/baseline.py`, which deliberately
  still calls `simulated_payment_provider` directly, never the resolved
  retry provider -- the comparison's point is "what would blind retry
  have done under the same simulated environment", not a second live
  Stripe call per case) were all updated to `await` it. `mypy --strict`
  caught both real call sites that needed the `await` added (`actions.py`,
  `baseline.py`) as a byproduct of the signature change, not a
  pre-existing bug.
- **`ActionExecutionOutcome` gained `REAL_SUCCESS` /
  `REAL_TEMPORARY_FAILURE` / `REAL_PERMANENT_FAILURE`**
  (`app/models/action.py`, additive -- stored as strings per ADR-005, no
  migration needed): a real bug caught by this session's own test suite
  before it shipped -- reusing `SIMULATED_SUCCESS` for a genuine Stripe
  result would have contradicted that value's own docstring ("the
  deterministic simulated provider ... reported the attempt succeeded").
  `ProviderAttemptResult` gained `is_real: bool = False`, the single
  explicit signal `execute_action` uses to choose between the
  `_SIMULATED_OUTCOME_BY_RESULT` / `_REAL_OUTCOME_BY_RESULT` tables --
  never inferred from `simulated_reference`'s string shape.
- **Frontend**: no code change needed -- `Action.executions[].outcome`
  was already typed as a plain `string` and rendered generically
  (`.replace(/_/g, " ")`), so `real_success` etc. display correctly with
  zero special-casing.
- **`.env.example` / `docker-compose.yml`**: `STRIPE_API_KEY` wired to
  the backend service, unset by default (falls back to simulated).
- **Real bug found and fixed by this session's own tests before it
  shipped**: `StripePaymentProvider.attempt` originally called
  `response.json()` unconditionally before checking
  `response.status_code >= 500` -- a 5xx body is not guaranteed to be
  JSON (a proxy error page, a truncated response), so this crashed with
  `JSONDecodeError` instead of returning the intended
  `REAL_TEMPORARY_FAILURE`. Caught by
  `test_stripe_server_error_is_a_real_temporary_failure`
  (`tests/test_providers_stripe.py`) failing on first run; fixed by
  checking the status code first, and a `try/except ValueError` was
  added around the still-possible 2xx-with-unparseable-body case too.

Verified: `tests/test_providers_stripe.py` (14 new tests, using
`httpx.MockTransport` -- no real Stripe account exists in this
environment, so these verify the request/response contract and
config/routing decisions, the parts entirely under this codebase's
control, not a live-key smoke test) plus the full pre-existing backend
suite, all passing; `ruff check` / `ruff format --check` / `mypy app`
(strict) all clean.

Explicitly NOT built in this phase, and why: a real
`request_payment_method_update` (Stripe Payment Link object) or
`contact_customer` (real email/SMS send) integration -- each is its own
integration surface, not exercised by "retry a failed payment," and was
out of scope by explicit owner confirmation; a live-key end-to-end smoke
test against a real Stripe test account (none exists in this
environment -- the request/response contract is verified via
`MockTransport` instead, the same limitation `docs/ai/local-model-setup.md`
already documents for the reasoning-model layer's own real-endpoint
testing); Stripe webhooks (this integration is synchronous
request/response only -- `execute_action` calls Stripe and reads the
`PaymentIntent` status back in the same request, so no webhook receiver
was needed or built).

## Phase 17 (2026-09-05)

Scope confirmed via AskUserQuestion before implementation: the master
plan's Phase 17 wishlist (dynamic strategy optimization, advanced model
routing, complex-case reasoning, human-in-the-loop, multimodal inputs,
autonomous experimentation, advanced recovery optimization) was audited
item by item. Six of seven require infrastructure this project has
already deliberately declined to fabricate: dynamic strategy
optimization / advanced recovery optimization need real ML training
data (ruled out since Phase 9, KI-007); advanced model routing beyond
Phase 10's failure-based routing would mean trusting model-reported
confidence, which ADR-006 already forbids; autonomous experimentation
needs a real A/B control group (ruled out since Phase 8/13,
EXPERIMENT_LIMITATION); multimodal inputs have no data source in this
system; complex-case reasoning risked crossing ADR-003 if it started
making decisions rather than only reasoning. Human-in-the-loop was the
one honestly buildable item, chosen over an "API-only, no UI" narrower
alternative.

A second AskUserQuestion, once implementation revealed the actual gap
(see below), confirmed the exact resolution shape: an operator resolves
a `pending_manual_review` case to `abandoned` or `failed` only, with a
required note -- never a full re-decision loop back into
`decision_pending` (would reopen decision/action identity questions)
and never `recovered` (no real evidence would exist).

**The real, pre-existing gap this phase closes**: `manual_review` was
bundled with `no_action` in
`app.decision.actions._NO_SIDE_EFFECT_ACTION_TYPES` -- both completed
the action immediately with no external side effect. For `no_action`
this is correct (the customer already paid, genuinely nothing further
is needed). For `manual_review` this was silently wrong: the Phase 5
policy engine's own escalation rules (fraud suspicion, insufficient
evidence, conflicting signals, retry-cap exhaustion) exist specifically
to route a case to a human, but no human was ever actually involved --
the case sailed through to `action_executed` and then `observing`
(almost always resolving `unresolved`, since no real attempt happened)
with nobody notified and no way to act on it.

What was built:

- **`RecoveryCaseState.PENDING_MANUAL_REVIEW`** (new enum value, migration
  `9fd7087e351e`, `ALTER TYPE ... ADD VALUE` -- the project's first enum
  value addition; downgrade cannot remove a single Postgres enum value
  without rebuilding the type, documented in the migration rather than
  attempted, since no data can exist in this new state immediately after
  a fresh upgrade in any normal migration sequence). New state-machine
  edges: `ACTION_SCHEDULED -> PENDING_MANUAL_REVIEW` and
  `PENDING_MANUAL_REVIEW -> {ABANDONED, FAILED}` only -- deliberately no
  edge back into `DECISION_PENDING` or forward into `RECOVERED`.
- **`app.decision.actions.execute_action`**: `manual_review` is now its
  own branch (previously bundled with `no_action`) -- the action itself
  still completes immediately with
  `ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED` (there is genuinely
  nothing for the executor to do), but the CASE transitions to
  `PENDING_MANUAL_REVIEW` instead of `ACTION_EXECUTED`.
- **`app.models.manual_review.ManualReviewResolution`** (migration
  `9fd7087e351e`): append-only, unique on `case_id` (a case can leave
  `PENDING_MANUAL_REVIEW` at most once -- the state machine has no edge
  back into it, so a second resolution attempt is a genuine conflict,
  unlike every other write in this system's pipeline which expects
  legitimate replays).
- **`app.recovery.manual_review.resolve_manual_review`** /
  `POST /recovery/cases/{id}/resolve-manual-review`: KI-008-safe
  (flush -> `IntegrityError` -> recheck), verified under 20-way
  concurrent identical requests (exactly one resolution, 19 `409`s).
- **Frontend**: a new "Manual review" panel on the case-detail page
  (`manual-review-panel.tsx` / `manual-review-action.ts`), shown only
  once a case is escalated, offering exactly `abandoned`/`failed` with a
  required note textarea -- never a `recovered` option.
- **Real, honestly-disclosed reachability gap found during
  implementation**: the only policy path producing an *approved* (not
  escalated) `manual_review` decision requires
  `retry_count >= RETRY_CAP`, but
  `app.decision.service._RETRY_COUNT_PENDING_PHASE_6` is hardcoded to
  `0` everywhere in the live system (no re-diagnosis loop exists yet --
  a pre-existing, already-documented gap). Every other `manual_review`
  path is `ESCALATED`, which `schedule_action` already rejects with
  `409` before `execute_action` is ever reached. This means the
  `PENDING_MANUAL_REVIEW` fix is structurally correct but currently
  unreachable through the live HTTP pipeline -- confirmed with the
  owner before proceeding rather than silently building around it. It
  becomes load-bearing the moment `retry_count` is ever made live.
  `tests/test_manual_review.py` documents this in its own module
  docstring and reaches the state the same way any test of
  currently-dead-but-correct code must: driving a case to a real
  diagnosed+decided state via the live HTTP flow, then directly
  updating that one persisted `DecisionResult` row to the exact
  combination the policy engine's own retry-cap-downgrade rule would
  itself produce.

Verified: `tests/test_manual_review.py` (13 new tests, including the
20-way concurrency case) plus the full pre-existing backend suite (785
total passing, 0 failures), `ruff check` / `ruff format --check` /
`mypy app` (strict) all clean; `alembic upgrade`/`check` clean (the one
remaining `alembic check` finding, `ix_domain_events_unpublished`, is a
pre-existing false positive on a partial index, confirmed present on
`main` before this phase's changes, not introduced by this migration);
frontend `eslint` / `tsc --noEmit` / `next build` / Vitest (73/73, +9
new) all clean; live Docker verification (`docker compose up --build`):
an unauthenticated/unknown-case resolve attempt returns `404` as
expected, migration applies cleanly on container startup.

Explicitly NOT built in this phase, and why: the other six Phase 17
wishlist items (see the scope-confirmation note above for the specific
reason each was excluded); a full re-decision loop
(`pending_manual_review -> decision_pending`) or an automatic timeout
for an unresolved manual review (both would be real, separate features
needing their own scoping pass, not folded into this one silently).

## Current Stage

N/A — Phase 17 closed (owner-authorized, 2026-09-05: "verify Phase 14,
then complete all remaining phases"). This was the last phase on the
roadmap (`docs/architecture.md`'s Phase Roadmap section, 0 through 17) --
no further phase is defined without new owner direction.

## Completed Phases

- Phase 0 — Engineering Foundation (frozen)
- Phase 1 — Data Foundation (frozen)
- Phase 2 — Revenue Risk Detection (frozen)
- Phase 3 — Recovery Case Management
- Phase 4 — AI Context & Diagnosis
- Phase 4.1 — Stabilization, AI Validation & Product Foundation
- Phase 5 — Recovery Decision & Policy Engine (5A–5I; frozen)
- Phase 6 — Action Executor (frozen)
- Phase 7 — Outcome Observation & Recovery Outcome (frozen)
- Phase 8 — Recovered Revenue Measurement (frozen)
- Phase 9 — Recovery Strategy Learning (scoped: dataset + analytics only;
  frozen)
- Phase 10 — AI Model Routing & Reliability (scoped: failure-based routing
  only, no confidence routing; frozen)
- Phase 11 — Historical Recovery Intelligence (scoped: deterministic
  structured-feature retrieval, no neural embeddings, no diagnosis-prompt
  change; frozen)
- Phase 12 — Asynchronous Event Architecture (outbox + Kafka audit/
  integration bus, no domain-logic trigger; frozen)
- Phase 13 — Analytics Data Platform (scoped: observed-only analytics; no
  incremental/experiment analytics, natural recovery disclosed as
  not-measurable rather than redefining Phase 8's outcome semantics;
  frozen)
- Phase 14 — Production Observability (structured JSON logging, request/
  domain correlation, operational + business metrics, liveness/readiness
  health checks, worker heartbeats; frozen)
- Phase 15 — Security & Fintech Hardening (scoped: API-key auth +
  role-based authorization, Redis-backed rate limiting, CORS policy,
  security headers; no end-user auth/session, no audit-trail
  tamper-evidence, no secrets-manager integration)
- Phase 16 — Real Payment Integration (scoped: `retry` channel only,
  Stripe TEST mode via `httpx`, config-gated with simulated fallback;
  `request_payment_method_update`/`contact_customer` remain simulated,
  no webhooks, no live-key smoke test)
- Phase 17 — Advanced Autonomous Recovery (scoped: human-in-the-loop
  manual review resolution only; new `pending_manual_review` state +
  resolution API/UI; currently unreachable via the live HTTP pipeline
  until `retry_count` is made live, disclosed rather than hidden; no
  ML/experimentation/multimodal features)

## Completed Stages (Phase 14)

Structured logging, correlation, metrics, and health (`app/core/logging.py`,
`app/core/middleware.py`, `app/core/metrics.py`, `app/core/heartbeat.py`,
`app/api/observability.py`, `app/api/health.py`). Four parts:

1. **Structured logging** (`app/core/logging.py`) — every log record
   through the root logger renders as one JSON object (timestamp, level,
   logger, message, `request_id` when inside a request, plus any
   `extra=` fields a caller passed). Domain services log the audit facts
   Section 51/Phase 14 ask for at their existing choke points --
   `app.services.diagnosis.diagnose_case` (case_id, diagnosis_id,
   outcome, disposition, model_name/version, prompt_version,
   schema_version, latency_ms, router_escalated),
   `app.decision.service.decide_case` (case_id, decision_id,
   diagnosis_id, decision_status, approved_strategy, recoverability),
   `app.decision.actions.schedule_action`/`execute_action` (case_id,
   action_id, action_type, execution_outcome), and
   `app.outcome.service.observe_outcome` (case_id, action_id,
   observation_id, outcome, is_terminal). Never logs customer email,
   payment external_reference, or AI reasoning free text -- every field
   is a bounded identifier or enum/numeric audit fact, the same boundary
   `app.events.handlers.EventAuditProjector` (Phase 12) already
   established.
2. **Request/domain correlation** (`app.core.middleware.RequestContextMiddleware`)
   — assigns/propagates `request_id` (`X-Request-Id` header in and out),
   logs "request completed"/"request failed" per call, and records
   operational HTTP metrics. Kept deliberately distinct from Phase 12's
   `DomainEvent.correlation_id` (case-scoped, spans many requests over a
   case's lifecycle) -- see `app.core.logging`'s module docstring for
   why collapsing the two would lose the actual cross-request trace this
   phase needs.
3. **Metrics** (`app.core.metrics`, `GET /metrics`, standard Prometheus
   text format) — operational: HTTP request count/latency by
   route-template+status (never the raw id-containing path, to bound
   cardinality), Phase 12 event pipeline counts
   (consumed/relayed by outcome). Business: revenue at risk / observed
   recovered revenue (per-currency gauges, KI-006-safe) / total recovery
   attempts, refreshed from Phase 8/13's own report functions on every
   scrape -- computes nothing new, just re-exposes those OBSERVED-only
   numbers as gauges.
4. **Health checks** (`app/api/health.py`) — liveness (`GET /health`,
   pre-existing from an earlier phase) vs. readiness (`GET /health/ready`)
   stay distinct; readiness now also probes Kafka but, per ADR-007,
   Kafka being unreachable never flips overall readiness (only
   PostgreSQL does) -- flipping it would be "readiness lying in the
   other direction", the same principle this endpoint's own prior
   docstring already established for Redis. The event relay/consumer
   (`scripts/event_relay.py`/`event_consumer.py`) have no HTTP server to
   probe, so `app/core/heartbeat.py` gives them a file-based liveness
   heartbeat instead, wired into `docker-compose.yml`'s `HEALTHCHECK`
   for both services (verified live: both report `(healthy)`).

Two real, pre-existing defects were found and fixed while building this
phase (not carried forward) -- see the 2026-09-03 owner-decisions entry
above for the full root-cause writeup: Alembic's `fileConfig()` silently
disabling every `app.*` logger in-process (`migrations/env.py`), and
uvicorn's own logging config silently overriding this phase's JSON
formatter in production (`app/main.py`'s lifespan startup handler).

## Completed Stages (Phase 13)

Analytics warehouse (`app/warehouse/`, `scripts/build_analytics_warehouse.py`,
migration `2620086aa88c`). Separates the analytical read path from the
operational tables it is derived from:

1. **Extract/transform/load** (`app/warehouse/etl.py`) — reads every
   `RecoveryCase` plus its `Payment`, latest `Diagnosis`,
   current-decision `RecoveryAction` (with executions), and current
   Phase 7 outcome (the exact same source tables and attribution rules
   Phase 8/9 already use), derives a denormalized `CaseAnalyticsFact` row
   per case, and upserts it keyed by `case_id` -- idempotent, safe to
   rerun, never accumulates duplicates or stale rows.
2. **Analytical storage** (`app/models/warehouse.py`,
   `case_analytics_facts`) — one wide fact table, deliberately not a
   star schema at this project's size; the only writer is the ETL, which
   is what keeps a later ClickHouse/BigQuery/Snowflake migration a
   load-step swap rather than a read-side rewrite.
3. **Read service / API** (`app/warehouse/service.py`,
   `GET /analytics/warehouse/facts`, `GET /analytics/warehouse/report`,
   `POST /analytics/warehouse/rebuild`) — revenue at risk, observed
   recovered revenue, total recovery attempts, recovery-rate breakdowns
   by strategy/failure-reason/customer-segment, and model performance
   (diagnosis count, avg confidence/latency, router-escalation rate) --
   all computed from the pre-built materialization, not live joins.
4. **Validation** — 16 tests covering idempotent rebuild (no duplicate
   facts on rerun), null-handling (a case with no diagnosis/action still
   produces a valid row), currency-safety (never mixes currencies),
   never-divide-by-zero rate computation, customer-segment bucketing,
   and UTC day-boundary/timezone handling.

Explicitly NOT done, by design: "incremental recovery" and "experiment/
control-treatment performance" (no counterfactual design exists --
`EXPERIMENT_LIMITATION`); "natural recovery" (Phase 7's outcome
observation is action-gated, so an action-less case cannot be honestly
classified `recovered` without diverging from Phase 8's own definition
for the same case -- `NATURAL_RECOVERY_LIMITATION`, see the 2026-09-02
owner decision above).

## Completed Stages (Phase 12)

Event architecture (`app/events/`, `scripts/event_relay.py`,
`scripts/event_consumer.py`, migration `c2d8f6a91e53`, ADR-007). Four
parts:

1. **Contract** (`app/events/schema.py`) — `DomainEvent`: `event_id`,
   `event_type`, `aggregate_id`, `aggregate_type`, `occurred_at`,
   `schema_version`, `source`, `correlation_id` (defaults to
   `aggregate_id` via `resolved_correlation_id()`), `payload` (bounded,
   typed fields only — never raw AI free text, never a raw ORM row).
2. **Outbox publication** (`app/events/publisher.py`,
   `app.recovery.service.open_case`/`transition_case`) —
   `OutboxEventPublisher.publish()` writes to the `domain_events` table
   inside the caller's own transaction, never opens or commits its own;
   an event is durable exactly when, and only when, the state change it
   describes is.
3. **Relay** (`scripts/event_relay.py`) — polls unpublished rows (partial
   index `ix_domain_events_unpublished`), publishes to Kafka keyed by
   `aggregate_id`, marks `published_at` only after a Kafka ack; if Kafka
   is down the outbox just grows and requests keep succeeding.
4. **Idempotent/retried/dead-lettered consumption**
   (`app/events/consumer.py`, `app/events/handlers.py`,
   `scripts/event_consumer.py`) — `process_event()` is DB-authoritative
   idempotent (`processed_events`, unique on `(event_id,
   consumer_group)`), retries a failing handler up to
   `settings.event_consumer_max_attempts` times, and dead-letters
   (`dead_letter_events`, with `error`/`attempts`) rather than retrying
   forever or silently dropping. The wired handler,
   `EventAuditProjector`, is read-only by design (a structured log line)
   so it cannot duplicate a business effect on redelivery.

Explicitly NOT done, by design: no domain service subscribes to Kafka to
trigger further domain logic (ADR-007 forbids this — it would reopen the
unsafe-dual-write/duplicate-effect problem the outbox exists to close);
Kafka availability never affects request-serving (`KafkaUnavailableError`
only affects the relay/consumer processes).

## Completed Stages (Phase 11)

Historical Case Retrieval (`app/retrieval/`, migration `b1c4e9a72f38`).
Three parts:

* **Deterministic "embeddings"** (`app/retrieval/embedding.py::compute_case_features`)
  -- a pure function (no randomness, no external call, no model) over a
  case's already-recorded disposition/outcome (one-hot), model-reported
  confidence, consecutive failures and historical success rate (reused
  verbatim from `app.risk.features.compute_risk_features`, not
  re-derived), and log-scaled payment amount. Explicitly NOT a learned/
  neural embedding -- no embedding-model endpoint exists in this
  repository, the same infrastructure gap KI-002 documents for the
  reasoning-model layer.
* **Vector storage & similarity search** (`CaseFeatureVector`, one row
  per `(case_id, diagnosis_id)` -- mirrors `DecisionResult`'s identity
  shape; plain-Python cosine similarity, no `pgvector`/new DB extension).
  `ensure_case_features` is idempotent (KI-008 discipline: flush ->
  `IntegrityError` -> rollback -> recheck). `find_similar_cases` lazily
  backfills every other diagnosed case's vector on first use as a
  candidate -- a real bug (candidates were never populated, so retrieval
  always returned empty) was found and fixed by the session's own
  retrieval-correctness test before this shipped.
* **Retrieval context, scoped** -- `GET /recovery/cases/{id}/similar-cases`
  is a read-only, operator-facing "similar past cases" lookup. It does
  NOT modify Phase 4's frozen diagnosis prompt/context-builder pipeline
  (no new prompt version, no change to the four-layer prompt-injection
  boundary `tests/test_ai_prompt_injection.py` already covers) --
  historical-case retrieval and similarity search are fully realizable
  without reopening that frozen pipeline, so this module does not.

"Retrieval evaluation" is a correctness check of the ranking mechanism
(`test_matching_disposition_and_outcome_ranks_above_a_dissimilar_case`),
never a claim about improved diagnosis accuracy -- KI-007 applies with
equal force here. Frontend: a new "Similar past cases" panel on the
case-detail page (only fetched once a case is diagnosed), reusing
existing table/`Panel` conventions, explicitly labeled "not a learned/
neural embedding, not a prediction of this case's outcome. Advisory
only." A real test-infrastructure bug in the frontend's route-stub
matcher was found and fixed in the same pass: a broad stubbed key like
`"/recovery/cases/"` was swallowing the more specific `.../similar-cases`
sub-route via substring matching regardless of insertion order; the
matcher now prefers an exact URL-suffix match. Tests:
`test_case_retrieval.py`, 9 backend tests (stable across 3 repeated
runs) + 1 new frontend test, zero regressions in the full suite.

## Completed Stages (Phase 10)

Model Router & Reliability (`app/ai/providers/factory.py::select_reasoning_model`,
`app/ai/providers/router.py`, `app/ai/report.py`, migration
`9a3e7b5c1d24`). Three parts:

* **Explicit/observable provider selection** -- `select_reasoning_model`
  performs identical resolution to the frozen `get_reasoning_model` (both
  always agree) but returns a `ProviderSelection`
  (`requested_provider`/`resolved_provider`/`substituted`/
  `substitution_reason`) and logs a structured warning at the moment of
  substitution, closing KI-009 (moved to Resolved).
* **Failure-based escalation** (`run_diagnosis_with_failover`) -- on a
  transport failure (`ReasoningModelError`) from the configured provider,
  retries once against `MockProvider` (always available). Deliberately
  NEVER escalates on the model's self-reported confidence (owner decision
  this session, mirroring Phase 9's own ADR-006/KI-007 discipline) --
  asserted structurally by a test that inspects the function's own
  signature for a confidence/threshold/probability parameter and finds
  none. `run_diagnosis` itself (the frozen Phase 4 contract several
  existing tests depend on) is untouched; the router wraps it, it does
  not replace it. `diagnose_case`'s existing `get_reasoning_model()` call
  and its monkeypatch hook are unchanged, so all frozen diagnosis tests
  (43) pass unmodified.
* **Real usage reporting** (`GET /ai/providers`, `GET /ai/model-report`,
  `scripts/benchmark_diagnosis.py --compare`) -- "model comparison" /
  "latency monitoring" / "model evaluation" computed from actually
  persisted `Diagnosis` rows only, never the synthetic evaluation set
  (KI-007); an empty diagnosis table produces a genuinely empty report,
  never a fabricated baseline. `Diagnosis.router_escalated` /
  `.router_escalation_reason` (new, additive, default-`false` columns)
  durably record a runtime escalation on the diagnosis row itself.
  `--compare` in the offline benchmark script shows `requested_provider`
  next to the actually-serving `provider` so a config-time substitution
  is visible in the comparison table itself, not hidden.

Frontend: the existing "AI diagnosis" panel (no new panel) gained a
router-status line (resolved provider, substitution warning if any, real
per-model diagnosis count/latency/escalation count) below its existing
advisory-only copy. Tests: `test_ai_model_router.py` (11) +
`test_ai_model_report.py` (4), all passing; zero regressions in the 43
frozen Phase 4 AI tests.

## Completed Stages (Phase 9)

Strategy Analytics (`app/analytics/`, no new persistence -- computes live
from `RecoveryCase` + `DecisionResult` + `RecoveryAction` + `Diagnosis` +
the current Phase 7 `RecoveryOutcomeObservation` per case, same pattern as
`app.measurement.service.get_revenue_report`). `GET
/analytics/strategy-dataset` returns the raw historical strategy dataset
(one row per case with an executed/scheduled action: strategy,
disposition, current outcome or `null`, currency) -- cases whose decision
was escalated/rejected and never reached scheduling are excluded (they
have no strategy to attribute). `GET /analytics/strategy-report`
aggregates it into empirical recovery-rate statistics by strategy and by
disposition: `recovered_count / observed_count`, `None` when
`observed_count` is 0 (never divides by zero, never fabricates a rate
from no evidence), with a `low_sample` flag (`LOW_SAMPLE_THRESHOLD = 5`,
purely informational -- drives no automated behavior, gates nothing,
unlike the forbidden confidence/high-value thresholds ADR-006/KI-006
already rule out at the policy layer). Deliberately does NOT implement an
ML recovery-probability model or strategy optimizer -- see the owner
decision above and `app/analytics/schema.py`'s module docstring; every
report carries `ml_model_status: "not_implemented"` and an explicit
`ml_model_limitation` string. Frontend: a new "Strategy analytics" panel
on the Overview dashboard (table of strategy -> recovered/observed ->
empirical rate -> sample-size flag, plus the ML-limitation disclosure
text), reusing existing `Panel`/`StatusPill`/`NotAvailableYet`
conventions -- no new dashboard, no duplicate of the Phase 8 "Recovery
performance" panel. Tests: `tests/test_strategy_analytics.py`, 9 tests
covering dataset correctness, escalated-decision exclusion,
divide-by-zero safety, low-sample disclosure, and an explicit scope-
boundary test that scans `StrategyAnalyticsReport`/`StrategyStat`'s own
field names for any probability/optimization/prediction/confidence/
ranking term and asserts none exist.

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

## Completed Stages (Phase 8)

Recovered Revenue Measurement (`app/measurement/`, migration
`4d8f0a2c6b91`): `RevenueMeasurement` — one idempotent, append-only-safe
row per `(case_id, outcome_observation_id)`, storing no amount/currency of
its own (both always read from the referenced `Payment` row — the sole
monetary source of truth; no measurement caller can supply, inflate, or
alter a figure). `measure_case` mirrors every prior phase's KI-008
idempotency discipline (flush → `IntegrityError` → rollback → recheck,
never a bare SELECT-then-INSERT); verified under 20-way concurrent
identical requests, exactly one row. `GET /measurement/report`
(`app/api/measurement.py`) computes a LIVE aggregate directly from
`RecoveryCase` + `DecisionResult` + `Payment` + the current Phase 7
`RecoveryOutcomeObservation` per case (same "compute live from source
tables" shape as `GET /risk/summary`), never from `RevenueMeasurement`
itself — avoids a staleness dependency on every case having had an
explicit `/measure` call. Every monetary field is a per-currency list
(`CurrencyAmount`/`BreakdownEntry`); nothing sums across currencies
(KI-006 remains open and unresolved by design). `RevenueReport` carries a
fixed `measurement_basis="observed_evidence"`, `counterfactual_available:
false`, and an explicit `counterfactual_limitation` string: this system
has no randomized control group, historical untreated cohort, or other
counterfactual design, so no incremental/causal recovered-revenue number
is computed or claimed anywhere — only observed facts (a later
successful/failed payment exists as evidence). Attribution reuses
Phase 7's own correlation rule exclusively (a case with no
`RecoveryOutcomeObservation` is not measurable — `409`, not a guess).
Frontend: the Overview dashboard's pre-existing "Recovery performance"
`NotAvailableYet` placeholder (explicitly labeled "Needs Phase 8 revenue
measurement" since Phase 4.1) is now filled with the real observed-
recovered figure, labeled "observed fact, not an estimate of impact."
Tests: `tests/test_revenue_measurement.py`, 18 tests covering recovered/
not-recovered/unresolved measurement, currency separation, attribution,
unrelated-payment rejection, duplicate/concurrent measurement (no double
counting), provenance, security (no client-supplied amount/currency/
status can become authoritative), and counterfactual-semantics
assertions — stable across 5 repeated runs including the 20-way
concurrency case.

## Completed Stages (Phase 7)

Outcome Observation (`app/outcome/`, migration `7c1b9e4f2a83`):
`ObservedOutcome` (`recovered`/`not_recovered`/`unresolved`) and
`RecoveryOutcomeObservation` (append-only, one row per observation
attempt, keyed `(action_id, attempt_no)` — mirrors
`RecoveryActionExecution`'s own shape deliberately). `observe_outcome`
classifies exclusively from authoritative `Payment` evidence: a later
`payment.succeeded`/`payment.failed` event for the same customer,
occurring after the originally failed payment's `occurred_at` — the
exact same deterministic relationship `app.decision.service`'s own
`already_paid` check and `app.risk.service`'s at-risk exclusion already
use; no new fuzzy matching, no time window, no confidence/monetary
threshold. Wires the two precondition checkers Phase 4.1 declared and
left open (`action_executed → observing`, `observing → recovered`) — no
new recovery-case states were introduced. A real idempotency bug was
found and fixed during implementation (by the session's own 20-way
concurrency test, not left latent): the idempotent-replay check
originally ran after the case-state gate, so a case that had already
reached the terminal `recovered` state rejected a repeat identical
observation with `409` instead of replaying it — fixed by reordering so
idempotency is checked first. Frontend: the case-detail page's existing
"7 · Outcome" panel (previously derived only from case state) was
enhanced in place — no duplicate outcome UI — to show
`recovered`/`not_recovered`/`unresolved` with evidence, distinctly from
the Action panel's `executed` status. Tests: `test_outcome_observation.py`,
15 tests, stable across 5 repeated runs including 20-way concurrency.

## Completed Stages (Phase 6)

Action Executor (`app/decision/actions.py`, models in
`app/models/action.py`, migration `3f2a6c9d1e47`): `schedule_action`
(`decision_pending → action_scheduled`) and `execute_action`
(`action_scheduled → action_executed`), both keyed to a case's
policy-approved `DecisionResult` only — escalated/rejected decisions get
`409`, never reach scheduling. `action_type` is always the decision's own
`approved_strategy`; neither function accepts a strategy parameter, so no
caller (including any future AI-facing surface) can choose what runs —
verified by a test that inspects the function signatures directly. No
payment-provider or customer-messaging integration exists anywhere in
this repository, and Phase 6 did not invent one:
`ActionExecutionOutcome.DEFERRED_NO_INTEGRATION` honestly records that a
`retry`/`contact_customer`/`request_payment_method_update` execution's
mechanical step completed without claiming money moved or a message was
sent; `no_action`/`manual_review` complete with
`NO_SIDE_EFFECT_REQUIRED`. Two real concurrency/correctness bugs were
found and fixed by the session's own 20-way concurrency tests (not left
latent): a `MissingGreenlet` hazard from reading expired ORM attributes
after `rollback()` (same class of defect as the Phase 5C root cause,
fixed the same way — capture IDs before rollback), and a subtler
SQLAlchemy identity-map staleness bug (a second query in the same session
does not refresh an already-loaded relationship collection without an
explicit `session.refresh()`). Frontend: new "5 · Action" panel mirroring
the Decision panel's conventions. Tests: `test_action_executor.py`, 13
tests, plus the previously-`xfail`ed Section-37
`test_contract_duplicate_action_is_idempotent` is now a real passing
assertion.

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
- Phase 7: KI-010 added, open (no dedicated test database —
  `DATABASE_URL` defaults to the shared dev Postgres; discovered when a
  routine full-suite run truncated the dev browser-QA fixture; mitigated
  each session since with an ad hoc `arr_test_db`, not yet a committed
  fix).
- Phase 8: KI-006 extended, still open and unresolved by design —
  `GET /measurement/report` never sums across currencies either (every
  field is a per-currency list). No new known issues introduced.
- Phase 9: no new known issue introduced. The master plan's "ML recovery
  model" / "recovery probability" / "strategy optimization" deliverables
  are deliberately not implemented (see the 2026-09-02 owner decision
  above and `app/analytics/schema.py`) — not a defect, a scoped and
  disclosed omission, the same discipline KI-007 already establishes for
  the diagnosis/decision evaluation harnesses.
- Phase 10: KI-009 RESOLVED (see `docs/known-issues.md`). No new known
  issue introduced. "Confidence routing" from the master plan's Phase 10
  definition is deliberately not implemented — see the 2026-09-02 owner
  decision above and `app/ai/providers/router.py`'s module docstring —
  not a defect, a scoped and disclosed omission extending ADR-006's own
  discipline to the model-routing layer.
- Phase 11: no new known issue introduced. Real neural embeddings from
  the master plan's Phase 11 definition are deliberately not implemented
  — see the 2026-09-02 owner decision above and
  `app/retrieval/schema.py`'s module docstring — not a defect, the same
  scoped-and-disclosed-omission discipline as Phase 9/10, extended to the
  retrieval layer.
- Phase 12: no new known issue introduced. KI-010 (no dedicated test
  database) remains open and applies equally to the new Phase 12 test
  file — `tests/test_events.py` was run against the same ad hoc
  `arr_test_db` as every other suite this session.
- Phase 13: no new known issue introduced. "Incremental recovery" /
  "experiment performance" and "natural recovery" from the master plan's
  Phase 13 definition are deliberately not implemented — see the
  2026-09-02 owner decision above and `app/warehouse/schema.py`'s module
  docstring — not a defect, the same scoped-and-disclosed-omission
  discipline as Phase 9/10/11, extended to the analytics-warehouse layer.
  KI-010 remains open and applies equally to `tests/test_warehouse.py`.
- Phase 14: no new known issue introduced. Two real defects discovered
  while building this phase were fixed, not left open -- see the
  2026-09-03 owner-decisions entry and Completed Stages (Phase 14) above
  (Alembic's `disable_existing_loggers` default silencing app loggers
  in-process; uvicorn's own logging config overriding this phase's JSON
  formatter in production). KI-010 remains open and applies equally to
  `tests/test_observability.py` / `tests/test_heartbeat.py`.

## Architecture Decisions

- ADR-001: PostgreSQL as source of truth
- ADR-002: Modular monolith
- ADR-003: LLM cannot directly execute financial actions
- ADR-004: Explicit recovery state machine with an append-only transition log
- ADR-005: Diagnosis output — two layers, derived disposition, everything versioned
- ADR-006: Model confidence is not a deterministic policy threshold (Phase 5)
- ADR-007: Asynchronous event architecture — outbox pattern, Kafka as an
  audit/integration bus only, never a domain-logic trigger (Phase 12)

## Last Successful Verification

2026-09-03 Phase 14 (Production Observability) complete (this session).
Backend: full suite **719 passed, 8 skipped (pre-existing), 1 xfailed
(KI-006), 0 failed** against the isolated `arr_test_db`, including 23
new Phase 14 tests (request-id propagation/non-leakage, structured JSON
formatting incl. no-PII-by-default, request-completion log correlation,
`/metrics` exposition and non-mutation, readiness's Kafka-visible-but-
non-blocking behavior, worker heartbeat freshness/staleness/missing/
garbage-content). `ruff check`, `ruff format --check`, `mypy app scripts`
(101 files) all clean. Docker: `backend`, `event-relay`, `event-consumer`
images rebuilt; `event-relay`/`event-consumer` both report `(healthy)`
via their new heartbeat `HEALTHCHECK`. Full live end-to-end verification
(not just unit tests): `GET /health/ready` against the running container
returned `{"database": "ok", "kafka": "ok"}` with overall `status:
"ready"`; `GET /metrics` returned real Prometheus-format operational
counters and business gauges (`arr_revenue_at_risk{currency="INR"}
10699.0`, etc., matching the Phase 13 warehouse's own numbers); a real
`POST .../diagnose` + `POST .../decide` call produced the exact expected
structured JSON log lines (`app.services.diagnosis` "case diagnosed" and
`app.decision.service` "case decided", each carrying `case_id` and the
correlating `request_id`, with model metadata on the diagnosis line) as
confirmed via `docker compose logs backend`, and no PII (customer email,
external reference, AI reasoning text) appeared in any of them. No Phase
15 code was introduced.

2026-09-02 Phase 13 (Analytics Data Platform, scoped) complete (this
session). Backend: full suite **704 passed, 8 skipped (pre-existing), 1
xfailed (KI-006), 0 failed** against the isolated `arr_test_db`,
including 16 new Phase 13 tests (idempotent rebuild/no duplicate facts,
null-handling for action-less/undiagnosed cases, currency-safety,
never-divide-by-zero rates, customer-segment bucketing, UTC day-boundary/
timezone handling, and explicit disclosure tests for both omitted
metrics). `ruff check`, `ruff format --check`, `mypy app scripts` (96
files) all clean. Migration `2620086aa88c` applied cleanly to both the
dev DB and `arr_test_db`. Docker: `backend` image rebuilt and verified
healthy at the new migration; full live end-to-end verification (not
just unit tests): `POST /analytics/warehouse/rebuild` against the
running container produced 8 real fact rows from data already in the
dev database, and `GET /analytics/warehouse/report` returned correctly
per-currency-bucketed revenue-at-risk/recovered figures, strategy/
failure-reason/customer-segment rate breakdowns, and model-performance
stats, with both `natural_recovery_status` and `experiment_status`
correctly disclosed as unavailable rather than fabricated. No Phase 14
code was introduced.

2026-09-02 Phase 12 (Asynchronous Event Architecture) complete (this
session). Backend: full suite **688 passed, 8 skipped (pre-existing), 1
xfailed (KI-006), 0 failed** against the isolated `arr_test_db`,
including 14 new Phase 12 tests (outbox writes on case-open/transition,
idempotent redelivery, retry-then-succeed, retry-exhaustion → dead
letter, correlation-id defaulting, publisher no-commit semantics, Kafka
transport unconfigured-broker edges). `ruff check`, `ruff format
--check`, `mypy app scripts` (89 files) all clean. Migration
`c2d8f6a91e53` applied cleanly to both the dev DB and `arr_test_db`.
Docker: `kafka` (single-node KRaft, `apache/kafka:3.8.0`) came up
healthy; `backend`/`event-relay`/`event-consumer` images rebuilt after
fixing a real defect the build surfaced (aiokafka's optional C
speedup extension needs `gcc`/`libc6-dev`/`zlib1g-dev`, absent from the
`python:3.13-slim` base — added to the Dockerfile, removed again after
`pip install` to keep the runtime image slim). Full live end-to-end
verification (not just unit tests): `POST /events` +
`POST /recovery/cases` against the running `backend` container wrote a
real `domain_events` row; `event-relay` published it to the real Kafka
broker and set `published_at`; `event-consumer` consumed it
(`outcome=handled attempts=1`, confirmed in its logs) and wrote the
`processed_events` idempotency marker — both confirmed directly via
`psql` against the dev database. No Phase 13/14 code was introduced.

2026-09-02 Phase 11 (Historical Recovery Intelligence, scoped) complete
(this session). Backend: full suite **674 passed, 8 skipped
(pre-existing), 1 xfailed (KI-006), 0 failed**, run against the isolated
`arr_test_db`; `test_case_retrieval.py` (9) stable across 3 repeated
runs, including a genuine implementation bug found and fixed mid-session
(candidate cases' feature vectors were never populated, so retrieval
always returned empty -- fixed by lazily backfilling every other
diagnosed case's vector on first use). `ruff check`, `ruff format
--check`, `mypy app` (76 files) all clean. Migration `b1c4e9a72f38`
(additive: new `case_feature_vectors` table) upgrade/downgrade/upgrade
roundtrip clean on both the dev and isolated DB, `alembic check` clean,
single head. Frontend: `vitest` 63/63 (a real test-infrastructure bug in
the route-stub matcher was also found and fixed in this pass -- see
Completed Stages below), `eslint` clean, `tsc --noEmit` clean, `next
build` clean. Real end-to-end verification: seeded three diagnosed cases
through the live API (two similar, one dissimilar), confirmed
`GET /recovery/cases/{id}/similar-cases` ranked the similar case above
the dissimilar one with real computed similarity scores, and confirmed
the case-detail page's "Similar past cases" panel rendered them correctly
with no console errors. No Phase 12 code was introduced.

2026-09-02 Phase 10 (AI Model Routing & Reliability, scoped) complete
(this session). Backend: full suite **665 passed, 8 skipped
(pre-existing), 1 xfailed (KI-006), 0 failed**, run against the isolated
`arr_test_db`, stable across 2 full runs; `test_ai_model_router.py` (11)
+ `test_ai_model_report.py` (4) all pass, zero regressions in the 43
frozen Phase 4 AI tests (`test_ai_diagnosis.py`, `test_ai_providers.py`,
`test_ai_failure_modes.py`, `test_diagnosis_api.py`). `ruff check`,
`ruff format --check`, `mypy app` (71 files) all clean (mypy hit a
transient environment failure mid-session -- `ImportError: DLL load
failed ... blocked by an Application Control policy` -- resolved by a
`pip install --force-reinstall mypy`; not a code issue, confirmed by full
subsequent clean runs). Migration `9a3e7b5c1d24` (additive:
`diagnoses.router_escalated`/`.router_escalation_reason`, default
`false`/`NULL`) upgrade/downgrade/upgrade roundtrip clean on both the dev
and isolated DB, `alembic check` clean, single head. Frontend: `vitest`
62/62, `eslint` clean, `tsc --noEmit` clean, `next build` clean. Real
end-to-end verification: monkeypatched a transport-unreachable primary
provider through the real `POST /recovery/cases/{id}/diagnose` endpoint
and confirmed automatic escalation to `mock` (`200`, not `502`,
`router_escalated: true`); confirmed `GET /ai/model-report` reflected the
escalation count; confirmed `scripts/benchmark_diagnosis.py --compare
mock,qwen` honestly showed `requested_provider=qwen,
provider=mock` (no `AI_QWEN_BASE_URL` configured) rather than hiding the
substitution. No Phase 11 code was introduced.

2026-09-02 Phase 9 (Recovery Strategy Learning, scoped) complete (this
session). Backend: full suite **650 passed, 8 skipped (pre-existing), 1
xfailed (KI-006), 0 failed**, run against the isolated `arr_test_db`;
`test_strategy_analytics.py` (9 tests) stable across 3 repeated runs.
`ruff check`, `ruff format --check`, `mypy app` (67 files) all clean. No
migration needed (pure live-computed layer, no new table -- verified via
`alembic check`, still a single head, no drift). Frontend: `vitest`
61/61, `eslint` clean, `tsc --noEmit` clean, `next build` clean. Real
end-to-end verification: confirmed `GET /analytics/strategy-report`
against real dev data matched the browser-rendered "Strategy analytics"
panel exactly (retry: 1/1 recovered, 100%, flagged "low sample"), no
console errors, no causal/probability language anywhere. No Phase 10 code
was introduced.

2026-09-02 Phase 8 (Recovered Revenue Measurement) complete (this
session). Backend: full suite **641 passed, 8 skipped (pre-existing), 1
xfailed (KI-006), 0 failed**, run against an isolated `arr_test_db` (not
the shared dev DB — KI-010); `test_revenue_measurement.py` (18 tests)
stable across 5 repeated runs including 20-way concurrent identical
`/measure` requests → exactly 1 `RevenueMeasurement` row. `ruff check`,
`ruff format --check`, `mypy app` (63 files) all clean. Migration
`4d8f0a2c6b91` upgrade/downgrade/upgrade roundtrip clean on both the dev
and isolated DB, `alembic check` clean, single head. Frontend: `vitest`
60/60, `eslint` clean, `tsc --noEmit` clean, `next build` clean. Real
end-to-end verification: seeded a recovered case and an unresolved case
through the live API, confirmed `POST /recovery/cases/{id}/measure`
returned the correct status for each, confirmed
`GET /measurement/report` showed per-currency observed-recovered/
not-recovered/unresolved totals with no cross-currency sum anywhere, and
confirmed the Overview dashboard's "Recovery performance" panel (a
`NotAvailableYet` placeholder since Phase 4.1) now shows the real
observed-recovered figure labeled "observed fact, not an estimate of
impact" — verified live in the browser, no console errors. No Phase 9
code was introduced.

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

`phase-14: implement production observability` — see `git log`.
(Preceded by `phase-13: implement analytics data platform` and every
phase back to `freeze: phases 0-2 verified`.)

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is raised to the project owner for
approval before proceeding.
