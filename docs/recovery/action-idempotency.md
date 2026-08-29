# Recovery Action Idempotency — design contract (Phase 4.1)

**Status:** design only. No action executor exists yet (Phase 6 builds it).
This document fixes the identity and idempotency rules *before* any code
can schedule or execute a financial action, so the executor is built
against a contract rather than retrofitted.

Related: `ADR-003` (the LLM never executes actions), `ADR-004` (explicit
state machine), `app/recovery/preconditions.py` (transition preconditions),
`app/services/ingestion.py` (the idempotency pattern already used for
inbound events).

## Why this matters

A recovery action is a real financial side effect (most often: ask the
processor to retry a charge). If the same action runs twice — because a
worker retried, a request was replayed, or a case was re-driven — the
customer can be charged twice. Idempotency is not optional here.

## Identities

| Name | Definition | Uniqueness |
|---|---|---|
| **Action identity** | What we intend to do, independent of attempts: `(recovery_case_id, action_type, decision_result_id)`. A case has at most one *open* action of a given type per decision. | One open action per `(case, action_type)` |
| **Execution identity** | One concrete attempt to carry out an action: `(action_id, attempt_no)`. | `attempt_no` strictly increases per action |
| **Idempotency key** | Deterministic string the executor sends to the downstream processor so *it* also dedupes: `f"arr:{recovery_case_id}:{action_type}:{attempt_no}"`. Stored on the execution row. | Unique per execution; safe to resend verbatim |

`decision_result_id` is in the action identity so that a *new* policy
decision (e.g. after re-diagnosis) can authorise a genuinely new action,
while a replay of the *same* decision cannot.

## Required behaviour

### Scheduling an action (`decision_pending → action_scheduled`)
1. Look up an existing action by action identity.
2. If one exists and is not in a terminal state → **return it, create
   nothing** (200-style idempotent hit). This is the "duplicate action"
   Section 37 case.
3. If none exists → create the action row (`status = scheduled`) and the
   transition, in one DB transaction. Unique constraint on
   `(recovery_case_id, action_type)` filtered to non-terminal status is
   the backstop against a concurrent double-schedule (same pattern as the
   `payments.external_reference` unique index).

### Executing an action (`action_scheduled → action_executed`)
1. Create an execution row with the next `attempt_no` and the derived
   idempotency key **before** calling the processor.
2. Call the processor, passing the idempotency key.
3. Record the processor response on the execution row. Never delete or
   mutate a prior execution row (append-only, like `ingestion_events` and
   `recovery_case_transitions`).
4. A transport failure/timeout is **not** proof the action did not happen.
   The next attempt reuses a *new* `attempt_no` but the processor-side
   idempotency key for the *same logical action* must let the processor
   collapse duplicates. (Exact key reuse-vs-rotate policy is a Phase 6
   decision recorded as an ADR.)

### Retry policy
- Bounded: at most `N` execution attempts per action (`N` from policy,
  mirrors the existing `"A payment may be retried at most 3 times"` rule
  in the context builder).
- Backoff between attempts (`recommended_delay_hours` is only an advisory
  input; the policy engine sets the real schedule).
- After `N` failed attempts → action `status = failed`, case may move to
  `FAILED` (a legal terminal edge from `ACTION_EXECUTED`).

### Observed outcome (`observing → recovered`)
- Only an **ingested `payment.succeeded` event for the same customer,
  occurring after the action was executed**, closes a case as
  `RECOVERED`. The observation is data we already receive through
  `POST /events`; Phase 7 links it to the case. This is the
  `preconditions.py` entry for `OBSERVING → RECOVERED`.

## What Phase 4.1 delivered toward this
- `app/recovery/preconditions.py` declares the artifact each forward
  transition depends on, including the action/execution/observation
  artifacts named above (checker `None` until the models exist).
- `tests/test_recovery_safety_contracts.py` pins the Section 37 cases
  (forbidden / duplicate / already-recovered / high-value) as executable
  specs.
- No executor, no scheduler, no processor client — those are Phase 6.
