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
