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

## AI Safety Architecture (introduced Phase 4+)

The LLM never executes financial actions directly. All AI output flows
through validation and policy layers before anything can act:

```
Database -> Context Builder -> LLM -> Structured Output -> Schema Validation
-> Policy Engine -> Recovery State Validation -> Idempotency Validation
-> Action Executor
```

See [ADR-003](decisions/ADR-003-llm-cannot-directly-execute-actions.md).

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
