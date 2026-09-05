# Architecture

## Overview

The AI Revenue Recovery Platform detects revenue at risk (e.g. failed
payments), diagnoses the cause, decides on a recovery strategy, executes
bounded recovery actions, observes outcomes, and measures recovered revenue.

Core loop:

```
DETECT -> DIAGNOSE -> DECIDE -> ACT -> OBSERVE -> UPDATE/LEARN -> MEASURE -> DETECT
```

## Style: Modular Monolith

The system starts as a single deployable backend service (FastAPI) organized
into internal modules with clear boundaries, so that any module can later be
extracted into its own service without a rewrite. See
[ADR-002](decisions/ADR-002-use-modular-monolith.md).

## Components (Phase 0)

- **backend/** — FastAPI application, Python 3.13, SQLAlchemy 2.0 (async),
  Postgres via psycopg3, Redis client.
- **frontend/** — Next.js 16 (App Router) + TypeScript + Tailwind.
- **postgres** — system of record. See
  [ADR-001](decisions/ADR-001-use-postgresql-as-source-of-truth.md).
- **redis** — cache and background job coordination (workers introduced in
  later phases).

## Recovery Case Management (Phase 3)

A **recovery case** is the unit of work for one at-risk payment. It moves
through an explicit state machine
(`detected -> diagnosing -> diagnosed -> decision_pending ->
action_scheduled -> action_executed -> observing -> recovered`, with
`abandoned` / `failed` terminal). State is changed only by a transition
service that validates every move against a single declared transition map
and appends an immutable `recovery_case_transitions` row for each change;
illegal transitions raise. One case per payment (`payment_id` unique).

No AI and no actions at this phase -- Phase 3 is the workflow spine the
later loop stages hang off. See
`docs/decisions/ADR-004-explicit-recovery-state-machine.md`,
`docs/api/recovery.md`, and `backend/app/recovery/`. Frontend: `/recovery`
(case list) and `/recovery/[id]` (case detail with transition history).

## AI Safety Architecture (introduced Phase 4+)

The LLM never executes financial actions directly. All AI output flows
through validation and policy layers before anything can act:

```
Database -> Context Builder -> LLM -> Structured Output -> Schema Validation
-> Policy Engine -> Recovery State Validation -> Idempotency Validation
-> Action Executor
```

See [ADR-003](decisions/ADR-003-llm-cannot-directly-execute-actions.md).

## AI Context & Diagnosis (Phase 4)

The reasoning model diagnoses *why* a payment failed — nothing more. A
`RecoveryContextBuilder` assembles a bounded, curated context from Postgres
(never raw rows); a pluggable `ReasoningModel` provider
(`mock` by default, or an OpenAI-compatible `qwen` / `nemotron` HTTP
endpoint) returns JSON; that JSON is schema-validated and passed through
cheap hallucination safeguards (`sparse -> unknown`, `conflict -> cap
confidence`) before a `Diagnosis` row is stored and the case advances
`detected -> diagnosing -> diagnosed`. If the model is unreachable or its
output is unusable, the case stays `diagnosing`, nothing is written, and
the API returns `502`.

The diagnosis has two layers: a specific `outcome` (the model's choice) and
a `disposition` **derived by our code** that Phase 5 will branch on. It
also carries an *advisory* recommended strategy — the policy engine (Phase
5) remains the authority, and nothing here executes anything (ADR-003).

Prompts and outputs are versioned (`diagnosis_prompt_v1`, `schema_version`
`"1"`), and every diagnosis stores its model/prompt/schema metadata and
latency for later comparison. A fixed evaluation set
(`backend/evaluation/diagnosis_cases.json`) and a benchmark runner
(`backend/scripts/benchmark_diagnosis.py`) compare providers.

See [ADR-005](decisions/ADR-005-diagnosis-schema-and-versioning.md),
`docs/ai/diagnosis.md`, `docs/ai/local-model-setup.md`, and
`backend/app/ai/`. Frontend: a diagnosis panel on `/recovery/[id]`.

## Bounded Recovery Action Execution (Phase 6)

`app/decision/actions.py::execute_action` executes ONLY an already
policy-approved `DecisionResult` (Phase 5), never a strategy chosen by a
caller. `no_action`/`manual_review` complete immediately with no side
effect. Every other approved strategy (`retry`,
`request_payment_method_update`, `contact_customer`) dispatches to a
deterministic, explicitly SIMULATED execution layer -- no real payment
gateway or messaging provider exists in this repository:

```
Action Executor -> app/decision/executors.py (RetryExecutor /
PaymentLinkExecutor / NotificationExecutor) -> app/decision/providers.py
(SimulatedPaymentProvider, a pure function of failure_reason + attempt_no)
```

Only this bounded executor layer may reach the simulated provider -- the
diagnosis model and the policy engine cannot import it, so ADR-003's
boundary is unaffected. A single action gets up to `RETRY_CAP` (the same
constant `app.decision.policy` defines) attempts, tracked as one
`RecoveryActionExecution` row per attempt; the loop lives entirely inside
one `execute_action` call/API request, so no new recovery-case state was
introduced. A simulated success creates a new `Payment` row (status
`succeeded`) plus an `IngestionEvent` audit row -- the identical shape any
other payment source produces -- and records the causal link explicitly
(`RecoveryActionExecution.resulting_payment_id`). Phase 7's
`observe_outcome` is unmodified and finds this evidence through its
existing later-successful-payment correlation rule, so the DETECT -> ACT
-> OBSERVE loop is now genuinely closed for the simulated path, never by
an unrelated event happening to arrive.

See `docs/recovery/action-idempotency.md` (design + what was actually
built), `docs/known-issues.md` (KI-012), and
`backend/tests/test_canonical_recovery_flow.py` for the full, causally
traced DETECT..MEASURE walk. Frontend: the action panel on
`/recovery/[id]` labels every outcome as simulated.

## Data Foundation (Phase 1)

See `docs/database/schema.md` for the schema. Summary: `customers`,
`payments` (one row per payment attempt, unique `external_reference`),
and an append-only `ingestion_events` log keyed by a caller-supplied
`idempotency_key`. `POST /events` is the single ingestion entry point;
redelivering the same idempotency key is a no-op, and reusing an
`external_reference` under a different key is rejected (`409`) rather
than silently overwritten.

## Revenue Risk Detection (Phase 2)

Rule-based (no ML/LLM yet -- that is Phase 9+): a failed payment is "at
risk" if the customer has no later successful payment. Risk features
(consecutive failures since last success, historical success rate,
failure-reason severity) combine into a deterministic score in `[0, 1]`
bucketed into low/medium/high. See `backend/app/risk/` for the
implementation and `docs/known-issues.md` KI-006 for a documented
limitation (no FX conversion across currencies yet).

Exposed via `GET /risk/payments` and `GET /risk/summary`, and a minimal
dashboard at frontend route `/risk`.

## Asynchronous Event Architecture (Phase 12)

Kafka is an audit/integration bus only -- never a trigger for domain
logic (ADR-007), which is what keeps this a modular monolith rather than
a set of services communicating over Kafka. An outbox pattern
(`domain_events` table, written in the same transaction as the case
state change it describes, via `app.recovery.service.open_case`/
`transition_case`) avoids an unsafe dual-write between Postgres and
Kafka. Two separate long-running processes, not part of the
request-serving backend, do the rest: `scripts/event_relay.py` drains
unpublished outbox rows to Kafka, and `scripts/event_consumer.py`
consumes them into `app.events.handlers.EventAuditProjector` via
`app.events.consumer.process_event`, which is DB-authoritative
idempotent (`processed_events`, unique on `(event_id, consumer_group)` --
KI-008 discipline), bounded-retried, and dead-lettered
(`dead_letter_events`) rather than retried forever or silently dropped.
See `docs/decisions/ADR-007-asynchronous-event-architecture.md`.

## Analytics Data Platform (Phase 13)

`app/warehouse/` separates analytical reads from the operational tables
they are derived from: `app/warehouse/etl.py` extracts from
`RecoveryCase`/`Payment`/`Diagnosis`/`RecoveryAction`/outcome data (the
same source tables and attribution rules Phase 8/9 already use) and
upserts one denormalized `CaseAnalyticsFact` row per case, keyed by
`case_id` -- idempotent, safe to rerun. `app/warehouse/service.py` reads
only that materialization for `GET /analytics/warehouse/report`, not
live joins. Deliberately does NOT compute "incremental recovery" or
"experiment/control-treatment performance" (no counterfactual design
exists in this system) or "natural recovery" (Phase 7's outcome
observation is action-gated, so computing it from raw evidence would
redefine Phase 8's own outcome semantics for the same case) -- both
disclosed via typed limitation fields on `AnalyticsWarehouseReport`
rather than fabricated.

## Production Observability (Phase 14)

Structured JSON logging (`app.core.logging`) for every log line, with a
per-request `request_id` (`app.core.middleware.RequestContextMiddleware`,
`X-Request-Id` header in and out) distinct from Phase 12's case-scoped
`DomainEvent.correlation_id` -- the former traces one HTTP call, the
latter traces a case's whole lifecycle across many calls over time.
Domain services log their existing choke points (diagnose/decide/
schedule/execute/observe) with case/decision/action/observation ids and
model metadata, never customer PII or AI reasoning text.
`GET /metrics` (Prometheus text format, `app.core.metrics`) exposes
operational HTTP/event-pipeline counters and business gauges re-derived
from Phase 8/13's own OBSERVED-only reports on each scrape.
`GET /health` (liveness) and `GET /health/ready` (readiness) stay
distinct; readiness also reports Kafka but never lets it flip overall
readiness (ADR-007: a Kafka outage never blocks request-serving). The
event relay/consumer have no HTTP server, so `app.core.heartbeat` gives
them a file-based liveness heartbeat instead, wired into
`docker-compose.yml`'s `HEALTHCHECK` for both.

## Security & Fintech Hardening (Phase 15)

Every mutating endpoint (ingest an event, transition/diagnose/decide/
schedule/execute/observe/measure a recovery case, rebuild the analytics
warehouse) now requires an `X-API-Key` header resolving to an `operator`
role (`app.core.auth`); every GET endpoint requires at least a
`readonly` key. `Settings.api_keys` parses `API_KEYS_RAW`
(`"key:role,key:role"`) once; an unknown/missing key is `401`, a known
key with insufficient role is `403` -- callers can tell "who are you"
apart from "you can't do that". `GET /health`, `GET /health/ready`, and
`GET /metrics` stay unauthenticated (infra probes/scrapers). Production
(`Settings.is_production`) refuses to start with no keys configured
(`app.main.create_app`) rather than silently serving every endpoint
open; development/test are exempt (every request is instead a real
`401` until keys exist -- there is no "auth disabled" mode).

`app/core/rate_limit.py::RateLimitMiddleware` is a Redis-backed
fixed-window limiter (one `INCR`+`EXPIRE` per request, no new
dependency -- `redis` was already declared for later phases and unused
until now), keyed by API key or client IP, exempting the same
unauthenticated health/metrics paths. Redis being unreachable fails
OPEN (logged, never silently swallowed), mirroring ADR-007's own
readiness principle that an infrastructure dependency that is not the
system of record must never block request-serving.
`app/core/security_headers.py::SecurityHeadersMiddleware` adds
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` on every
response, plus `Strict-Transport-Security` in production only. CORS
(`Settings.cors_allowed_origins`) is off by default -- the bundled
frontend calls the backend server-to-server, never from a browser
origin -- and only added when explicitly configured.

The frontend authenticates its own server-to-server calls with a single
operator-role `BACKEND_API_KEY` (`frontend/src/lib/backend-auth.ts`,
never exposed to the browser, never `NEXT_PUBLIC_*` -- same boundary as
`API_BASE_URL`, KI-001).

Deliberately NOT built in this phase: end-user login/session/OAuth (this
is a service-to-service backend with no end-user-facing login flow
anywhere in this codebase); tamper-evidence/hash-chaining of the audit
trail (a separate, not-yet-scoped hardening concern); secrets-manager
integration (API keys are environment variables, the same mechanism
every other credential in this system already uses).

## Real Payment Integration (Phase 16)

Scoped by explicit owner decision before implementation: only the
`retry` recovery strategy gets a real gateway.
`app/decision/providers_stripe.py::StripePaymentProvider` calls Stripe's
real `POST /v1/payment_intents` endpoint in **TEST mode only** (a
`sk_live_...` key is refused at construction --
`StripeConfigurationError`) via `httpx.AsyncClient` (never the
synchronous `stripe` SDK, which would block this codebase's asyncio
event loop). `select_retry_provider()` resolves it when
`Settings.stripe_api_key` is configured, otherwise falls back to the
existing `SimulatedPaymentProvider` -- the same config-gated,
fallback-not-fail shape `app/ai/providers/factory.py` already
established for the reasoning-model layer. `request_payment_method_update`
/ `contact_customer` are unchanged, still always simulated -- a real
payment-link object or a real customer-messaging send are each their
own integration surface, out of this phase's scope.

`PaymentProvider.attempt` is now `async` (a real gateway call is genuine
network I/O); `app.decision.actions.execute_action` awaits it.
`ActionExecutionOutcome` gained `REAL_SUCCESS` /
`REAL_TEMPORARY_FAILURE` / `REAL_PERMANENT_FAILURE`, kept distinct from
the pre-existing `SIMULATED_*` values -- `ProviderAttemptResult.is_real`
is the single explicit signal that chooses between them, so a genuine
Stripe result is never persisted under a value whose own docstring says
"simulated". ADR-003 is unaffected structurally, the same way it was for
the simulated provider: this module has no database access, no session,
and is never imported by `app.ai` or `app.decision.policy` -- only
`app.decision.executors` reaches it. See
`docs/recovery/action-idempotency.md`'s "What Phase 16 delivered"
section for the full account, including why the baseline-vs-AI
comparison (Phase 8) deliberately still calls the simulated provider
directly rather than the resolved retry provider.

## Advanced Autonomous Recovery / Human-in-the-Loop (Phase 17)

Scoped by explicit owner decision after auditing the master plan's Phase
17 wishlist (dynamic strategy optimization, advanced model routing,
complex-case reasoning, human-in-the-loop, multimodal inputs, autonomous
experimentation, advanced recovery optimization): every item except
human-in-the-loop requires infrastructure this project has already
deliberately declined to fabricate (real ML training data -- KI-007;
a real A/B control group -- KI-006/EXPERIMENT_LIMITATION; multimodal
payment data, which does not exist). Human-in-the-loop was the one
honestly buildable slice.

Closes a real gap this session's own audit found: a case whose approved
decision strategy is `manual_review` previously auto-completed through
`action_executed` exactly like `no_action` -- no operator ever actually
reviewed anything, despite the Phase 5 policy engine's own escalation
rules (fraud suspicion, insufficient evidence, conflicting signals)
existing specifically to route a case to a human.
`app.decision.actions.execute_action` now instead transitions such a
case to a new `RecoveryCaseState.PENDING_MANUAL_REVIEW` and stops.
`app.recovery.manual_review.resolve_manual_review` (exposed at
`POST /recovery/cases/{id}/resolve-manual-review`) is the only way such
a case can ever leave that state -- an operator resolves it to
`ABANDONED` or `FAILED` (never `RECOVERED`: no authoritative payment
evidence exists merely because a human looked at the case, and this
endpoint never invokes Phase 7's evidence-based outcome observation), with
a required note recorded on an append-only `ManualReviewResolution` row.
Deliberately NOT a full re-decision loop back into `decision_pending` --
that would reopen decision/action identity questions out of this
phase's scope; the state machine has no edge back into
`PENDING_MANUAL_REVIEW`, so a resolution is a one-time, non-idempotent
operation (a second attempt is a genuine `409` conflict, not a replay).

Honestly disclosed reachability gap, not glossed over: the only policy
path that produces an *approved* (rather than escalated -- which
`schedule_action` already rejects with `409` before ever reaching
`execute_action`) `manual_review` decision requires
`retry_count >= RETRY_CAP`, but `retry_count` is hardcoded to `0`
everywhere in the live system (`app.decision.service._RETRY_COUNT_PENDING_PHASE_6`
-- no re-diagnosis loop exists). This fix is therefore currently
unreachable via the live HTTP pipeline; it becomes load-bearing the
moment that constant becomes live. Tested by driving a case to a real
diagnosed+decided state via the live HTTP flow, then directly updating
that one persisted `DecisionResult` row to the combination the policy
engine's own retry-cap-downgrade rule would itself produce -- never
inventing a shape the policy engine could not itself produce. Frontend:
a new "Manual review" panel on the case-detail page, shown only once a
case is escalated, offering exactly the two safe resolutions.

## Phase Roadmap

See `docs/project-state.md` for current phase/stage status. The full ordered
phase list lives in the original engineering prompt provided by the project
owner and is mirrored here for reference:

0. Engineering Foundation
1. Data Foundation
2. Revenue Risk Detection
3. Recovery Case Management
4. AI Context & Diagnosis
5. Recovery Decision Engine
6. Action Execution
7. Observation & Closed Loop
8. Revenue Measurement
9. Strategy Learning
10. Model Routing & AI Reliability
11. Retrieval & Historical Intelligence
12. Asynchronous Event Architecture
13. Analytics Warehouse
14. Production Observability
15. Security & Fintech Hardening
16. Real Payment Integration
17. Advanced Autonomous Recovery
