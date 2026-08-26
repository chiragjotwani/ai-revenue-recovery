# ADR-001: Use PostgreSQL as the Source of Truth

## Context

The platform makes financial decisions (recovery actions on real or
simulated payments). It needs a durable, transactional, strongly-consistent
store that can enforce constraints (foreign keys, uniqueness, idempotency
keys) and that every other component — including the AI layer — treats as
authoritative.

## Decision

PostgreSQL is the single source of truth for customer, payment, event, and
recovery-case state. No other store (cache, vector index, LLM context, or
analytics warehouse) may be treated as authoritative. Redis and any future
vector store are derived/auxiliary and must be rebuildable from Postgres.

## Alternatives Considered

- MongoDB: weaker transactional/constraint guarantees for financial state.
- Event-sourced store only (e.g. EventStoreDB): adds operational complexity
  not justified at this phase; revisited if/when Phase 12 introduces an
  event-driven architecture.

## Reasoning

Financial correctness requires ACID transactions, foreign key integrity,
and unique constraints (e.g. idempotency keys). PostgreSQL is mature,
well-understood, and sufficient at current and near-term scale.

## Consequences

- All AI-derived or cached data must be reconcilable against Postgres.
- Migrations (Alembic) are mandatory for every schema change.
- Any future move to event sourcing or a warehouse must treat Postgres
  writes as the point of truth that downstream systems replay from.
