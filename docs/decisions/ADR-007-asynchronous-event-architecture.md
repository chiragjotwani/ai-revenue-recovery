# ADR-007: Asynchronous Event Architecture (Phase 12)

## Context

ADR-002 (modular monolith) explicitly reserved this point ("see Phase 12
for the point at which async/event infrastructure is introduced") for
introducing asynchronous event infrastructure. Phase 12's brief calls for
Kafka, but is explicit that Kafka must not be assumed to be required
everywhere and must be introduced only where justified, and that the
existing synchronous interfaces must be preserved unchanged.

The repository's existing synchronous request/response pipeline (ingest ->
risk -> case -> diagnose -> decide -> schedule -> execute -> observe ->
measure) is the sole, tested, frozen authority for every business effect
through Phase 11. Nothing about Phase 12 may create a second path that
could independently trigger, duplicate, or diverge from that pipeline's
decisions -- that would violate ADR-003 (no execution authority outside
the validated chain) and the "no duplicate business effects on redelivery"
requirement in the same stroke.

## Decision

1. **The modular monolith is preserved.** All business logic remains in
   one deployable FastAPI backend. Kafka is introduced as an
   **integration/audit event bus** for OTHER systems and read-side
   projections to consume -- it is never the trigger for a
   state-changing domain operation. The synchronous HTTP API remains the
   only way to open a case, diagnose, decide, schedule, execute, observe,
   or measure. This is what makes "no duplicate business effects" true by
   construction: an event consumer can only ever produce a read
   projection (e.g. an audit log row), never re-run or fork business
   logic.

2. **Outbox pattern**, not a dual write. A domain event is written to a
   Postgres `domain_events` table in the exact same transaction as the
   state change that produced it (`app/events/publisher.py::OutboxEventPublisher`).
   This guarantees the event and the state change are atomic and
   consistent with each other (ADR-001: Postgres is the source of truth) --
   there is no window where one could be persisted without the other.
   A separate **relay** process (`scripts/event_relay.py`) reads
   unpublished outbox rows and publishes them to Kafka, marking them sent.
   If the relay is down, events queue safely in Postgres; nothing is
   lost, and nothing is double-applied to the domain when it comes back.

3. **Domain code depends on an abstract `EventPublisher`, never Kafka
   directly.** `app/events/publisher.py` defines the interface;
   `OutboxEventPublisher` is the only implementation domain services call.
   Kafka-specific code (`app/events/kafka.py`) is confined to the relay
   and the consumer runner -- neither is imported by any `app.decision`,
   `app.outcome`, `app.measurement`, or `app.recovery` module.

4. **Idempotent, retried, dead-lettered consumption.** Every consumed
   event is recorded in a `processed_events` table (unique on `event_id`)
   before its handler runs; a redelivered event is a no-op by database
   constraint, not an application-level pre-check (the same KI-008
   discipline every prior phase's service module already applies).
   Handler failures are retried a bounded number of times with backoff;
   exhausting retries writes to a `dead_letter_events` table rather than
   silently dropping or endlessly retrying the event.

## Alternatives Considered

- Kafka as the trigger for domain state changes (event-driven domain
  logic): rejected -- this would create a second authority alongside the
  synchronous API, directly risking duplicate/divergent business effects
  and contradicting the validated chain ADR-003 already establishes.
- Direct dual write (write to Postgres, then publish to Kafka in the same
  request, no outbox): rejected per the Phase 12 brief's own instruction
  -- a crash between the two writes would silently lose the event or
  create an inconsistency Postgres alone cannot detect.
- Splitting the backend into separate services around the event bus:
  rejected -- no scale or team-size justification exists yet (the same
  reasoning ADR-002 already gives), and Phase 12's brief does not require
  it either.

## Reasoning

This shape gets the real value Phase 12 asks for (a durable,
replayable, at-least-once event stream that other systems -- including
Phase 13's analytics warehouse -- can consume) without weakening any
frozen invariant: the synchronous pipeline remains the sole business
authority, Postgres remains the source of truth, and Kafka failure modes
(broker down, consumer lag, redelivery) cannot corrupt or duplicate a
recovery case's actual state.

## Consequences

- Every domain service that should emit an event takes on one additional,
  same-transaction outbox write. This is additive to each frozen
  service's existing behavior, never a change to its return value, state
  machine, or existing tests.
- The relay and consumer are additional operational processes (Docker
  services) with their own health checks, distinct from the request-serving
  backend.
- A Kafka outage degrades to "events queue in Postgres, audit/analytics
  consumers fall behind" -- it can never degrade to "the recovery pipeline
  stops working," because the recovery pipeline never depends on Kafka.
