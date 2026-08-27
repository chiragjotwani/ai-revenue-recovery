# ADR-004: Recovery Cases Use an Explicit State Machine with an Append-Only Transition Log

## Context

Phase 3 introduces the recovery case: the unit of work that tracks a single
at-risk payment from detection through to a terminal outcome. Later phases
(diagnosis, decision, action execution, observation, measurement) each
advance a case through part of a lifecycle:

```
DETECTED -> DIAGNOSING -> DIAGNOSED -> DECISION_PENDING
         -> ACTION_SCHEDULED -> ACTION_EXECUTED -> OBSERVING -> RECOVERED
```

with `ABANDONED` and `FAILED` as terminal outcomes.

Section 16 of the engineering prompt requires that all state transitions be
explicitly defined, that state never be assigned arbitrarily, and that
illegal transitions raise. Section 15 requires that state changes be
traceable.

## Decision

1. **A single source of truth for legal transitions.**
   `backend/app/recovery/state_machine.py` holds `LEGAL_TRANSITIONS`
   (a mapping of state to its permitted next states), `TERMINAL_STATES`,
   and `INITIAL_STATE`. Nothing else encodes the lifecycle.

2. **All state changes go through a transition service.**
   `backend/app/recovery/service.py::transition_case` is the only code that
   writes `RecoveryCase.state`. It validates against the state machine and
   raises `IllegalStateTransitionError` (surfaced as HTTP `409`) for any
   transition not in the map. On the illegal path nothing is written.

3. **Every transition is recorded immutably.**
   Each successful change appends a `recovery_case_transitions` row
   (`from_state`, `to_state`, `reason`, `actor`, `created_at`) in the same
   database transaction as the update to `recovery_cases.state`. These rows
   are never updated or deleted by application code. The first row for a
   case has `from_state = NULL` and records entry into `DETECTED`.

4. **One case per payment.**
   `recovery_cases.payment_id` is unique. Opening a case for a payment that
   already has one returns the existing case (HTTP `200`) rather than
   creating a second (HTTP `201` only on first open).

5. **The full lifecycle enum is defined now, not grown per phase.**
   All ten states exist from Phase 3 even though Phase 3 only drives the
   earliest transitions. This avoids a Postgres `ALTER TYPE` migration in
   every subsequent phase and gives later phases a stable contract.

## Alternatives Considered

- **A free `status` string updated in place.** Rejected: no enforcement of
  legal transitions, no history, easy to land in an impossible state.
- **Deriving state from the presence of related rows** (a diagnosis row
  implies `DIAGNOSED`, etc.). Rejected: the mapping becomes implicit and
  order-dependent, and terminal/abandoned outcomes have no natural row to
  key off. An explicit column with an audit log is simpler to reason about
  and to test.
- **Defining only the states Phase 3 uses and extending the enum later.**
  Rejected (see decision 5): more migrations, and later phases would be
  building against a moving contract.
- **A retry back-edge (`OBSERVING -> ACTION_SCHEDULED`) now.** Deferred:
  the Phase 3 map is strictly linear plus terminal edges. A retry loop
  will be added when Phase 6/7 actually needs multiple recovery attempts,
  with its own tests.

## Consequences

- Phase 4+ advance a case only by calling `transition_case`; they never
  set `state` directly. Adding a new legal transition means editing
  `LEGAL_TRANSITIONS` and its tests, nothing else.
- The transition log is the audit trail Section 15 requires and the basis
  for later revenue-measurement and learning phases (time-in-state, time
  to recovery).
- `RecoveryCase` currently carries no diagnosis/decision/action data —
  those are added by their own phases as related tables, each gated by the
  state they correspond to.
- Illegal-transition behaviour is covered by
  `backend/tests/test_recovery_state_machine.py` (pure) and
  `backend/tests/test_recovery_api.py` (through the API).
