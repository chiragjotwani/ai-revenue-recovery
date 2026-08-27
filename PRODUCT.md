# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Undecided (explicit open decision, not inferred). Two candidate shapes on
the table:

1. An internal ops/finance tool used by a single company to monitor and
   recover its own failed-payment revenue (single-tenant).
2. A product sold to other businesses, who each use it to recover their
   own customers' failed payments (multi-tenant SaaS).

This choice has architectural consequences (auth/RBAC model, tenancy,
whether the dashboard needs per-customer scoping) that later phases
(Phase 15 Security & Fintech Hardening, in particular) will need
resolved. Revisit before that phase, or sooner if UI work depends on it.

## Product Purpose

Detects revenue at risk (failed/at-risk payments), diagnoses why revenue
is at risk, decides on and executes a bounded recovery strategy, observes
the outcome, and measures actual recovered revenue -- a closed
detect -> diagnose -> decide -> act -> observe -> learn -> measure loop.
Success is revenue recovered that would otherwise have been lost to
payment failure, with full auditability of every step that led there.

## Positioning

Self-hosted, open-weight AI models for diagnosis and strategy -- no data
leaves the deployment to a paid third-party LLM API. Paired with a hard
architectural guarantee: the AI can never directly execute a financial
action. Every AI-influenced decision passes through schema validation, a
deterministic policy engine, recovery-state validation, and idempotency
checks before anything executes; insufficient evidence must resolve to
`UNKNOWN` rather than a guess. The competitive claim is data control and
auditability, not raw model capability.

## Operating Context

Payment lifecycle events (succeeded/failed/pending) are ingested via a
single API entry point and are the trigger for the whole loop. The
platform currently operates on synthetic/seeded data for development;
there is no live payment processor integration yet (planned for Phase 16,
gated behind simulation-mode verification and a feature flag -- real
payment execution is never on by default).

## Capabilities and Constraints

Built so far (Phases 0-2 of a 17-phase build; see
`docs/project-state.md` for current status):

- Payment/customer/event ingestion, idempotent on a caller-supplied key,
  with immutable audit logging of every ingested event.
- Rule-based (not yet ML/AI) revenue risk detection and scoring, exposed
  via API and a minimal dashboard.

Explicitly not yet built: recovery case state machine, AI diagnosis,
decision/policy engine, action execution, outcome observation, revenue
measurement, and strategy learning (Phases 3-9+).

Hard constraints carried through every future phase:

- Postgres is the sole source of truth; the AI is never authoritative and
  never invents customer history, payment status, or prior actions.
- No paid LLM APIs unless explicitly instructed otherwise.
- Every financial action must be authorized, policy-checked, state-
  checked, idempotent, and auditable; simulation mode precedes any real
  money movement.

## Evidence on Hand

No real customer data, brand assets, testimonials, or case studies exist.
The only data in the system is a synthetic canonical scenario used for
development and testing: a customer with 3 historically successful
payments, then 1 failed payment of 4999.00 (currency INR) due to
insufficient funds (`backend/scripts/seed_synthetic_data.py`). Do not
treat this as real evidence or extend it into marketing claims.

## Product Principles

1. The database is the source of truth; the AI layer is advisory and
   bounded, never authoritative and never directly executing.
2. Prefer simple, modular, and testable over premature infrastructure --
   a modular monolith first, additional infrastructure only when its
   benefit is demonstrated (see `docs/decisions/ADR-002`).
3. Every claim of "done" is backed by actual verification (tests run,
   builds executed, migrations applied) -- never asserted without
   evidence.
4. Nothing ships silently: known limitations, bypasses, and open
   decisions are recorded (`docs/known-issues.md`), never hidden.

## Accessibility & Inclusion

No product-specific requirement established yet.
