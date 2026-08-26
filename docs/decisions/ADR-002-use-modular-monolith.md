# ADR-002: Use a Modular Monolith

## Context

The system will eventually need many capabilities (risk detection, recovery
case management, AI diagnosis, decisioning, action execution, learning,
analytics). Building these as separate services from day one adds
distributed-systems complexity (network calls, partial failure, service
discovery, deployment orchestration) before there is any load or team-size
reason to pay that cost.

## Decision

Build a single deployable backend (FastAPI) organized into internal modules
with explicit boundaries (e.g. `risk/`, `recovery/`, `ai/`, `actions/`,
`measurement/`). Modules communicate via well-defined Python interfaces and
the shared Postgres schema, not ad hoc imports across boundaries.

## Alternatives Considered

- Microservices from the start: rejected — no current scale or team
  justification, and it would slow down every phase of this build.
- Single unstructured script/module: rejected — would prevent later
  extraction and encourage tangled dependencies.

## Reasoning

A modular monolith gets nearly all the maintainability benefits of service
boundaries without the operational cost, while keeping the door open to
extract a module into its own service later if scale demands it (see
Phase 12 for the point at which async/event infrastructure is introduced).

## Consequences

- Module boundaries must be respected in code review even though there is
  no network boundary enforcing them.
- Extraction to services later requires the module's persistence and
  interface to already be clean — this is a design constraint, not an
  afterthought.
