# Recovery Action Idempotency — design contract (Phase 4.1)

**Status:** implemented (Phase 6, completed 2026-09-03). This document
fixed the identity and idempotency rules *before* any code could schedule
or execute a financial action, so the executor was built against a
contract rather than retrofitted. See "What Phase 6 completion delivered"
below for what was actually built, including where the original design
here was superseded rather than followed literally (the retry-policy and
processor sections below).

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
- No executor, no scheduler, no processor client — those were Phase 6's.

## What the original Phase 6 commit (2026-09-01) delivered — and what it left open

The action/execution identities and the scheduling contract above were
implemented exactly as designed (`app/decision/actions.py`,
`app/models/action.py`). What was **not** built at that time: there was no
"processor" of any kind (real or simulated) to call in step 2 of
"Executing an action" above — every non-`no_action`/`manual_review`
strategy recorded `ActionExecutionOutcome.DEFERRED_NO_INTEGRATION` and
stopped, honestly, rather than fabricating a result. This meant the
"Retry policy" and "Observed outcome" sections above described a loop that
never actually ran: `attempt_no` never exceeded 1, and nothing this system
did ever produced the `payment.succeeded` evidence Phase 7 needed —
recovery only ever happened when an unrelated, independently-ingested
event happened to arrive.

## What Phase 6 completion (2026-09-03) delivered

A deterministic, explicitly SIMULATED provider layer closes that gap
without touching ADR-003 or the state machine:

- `app/decision/providers.py` — `SimulatedPaymentProvider`: a pure
  function of `(failure_reason, attempt_no)`, no network call, every
  reference/detail string clearly prefixed `sim:`.
- `app/decision/executors.py` — `RetryExecutor` /
  `PaymentLinkExecutor` / `NotificationExecutor`, one per real-side-effect
  strategy, each a thin wrapper over the provider above. Only
  `app/decision/actions.py::execute_action` calls these — neither the
  diagnosis model nor the policy engine can reach them.
- **`RecoveryAction.status` semantics, revised from the design above**:
  `status` becomes `executed` on EVERY terminal outcome -- success,
  permanent failure, or retry-cap exhaustion -- never `failed`. It means
  "the execution process completed", the exact meaning
  `app/recovery/preconditions.py::_requires_executed_action` already
  checked for the `action_executed -> observing` transition before this
  completion existed; introducing a second status value here would have
  broken that existing, unmodified Phase 7 precondition. Whether an
  attempt succeeded lives entirely on the execution row's own `outcome`
  field (`simulated_success` / `simulated_temporary_failure` /
  `simulated_permanent_failure`), which Phase 7 reads honestly by finding
  (or not finding) resulting payment evidence -- never by a second,
  competing status vocabulary on the action itself.
- **Retry policy, revised from the design above**: rather than a
  processor-side idempotency key sent to an external system (there is
  still no external system), the bounded loop lives entirely inside
  `execute_action` itself, capped at `RETRY_CAP` (imported from
  `app.decision.policy`, the same constant, not a second one) attempts.
  A temporary-failure attempt leaves the action `scheduled` (not
  `failed`) so a further `execute_action` call attempts the next attempt
  — no new `RecoveryCaseState` or state-machine edge was needed for this.
- **Observed outcome, exactly as designed**: a `SIMULATED_SUCCESS`
  attempt creates a new `Payment` (status `succeeded`) + `IngestionEvent`
  row — the identical shape any other payment source produces — inside
  the same transaction as the execution attempt.
  `RecoveryActionExecution.resulting_payment_id` records the causal link
  explicitly. Phase 7's `observe_outcome` is **completely unmodified** and
  finds this new payment via its existing later-successful-payment
  correlation rule.
- Never claims a real payment gateway or messaging provider was
  contacted, anywhere — in code, in API responses, or in the frontend
  (`frontend/src/app/recovery/[id]/action-panel.tsx`).

See `tests/test_canonical_recovery_flow.py` for the full DETECT..MEASURE
walk this produces, and `tests/test_action_executor.py` for the bounded
multi-attempt / permanent-failure / retry-cap unit-level coverage.

**Still not built, deliberately out of scope for this completion**: a
real payment-gateway or messaging integration (would be a Phase 15+
concern); a case-level re-diagnosis loop after a fully failed action
(`app/decision/service.py`'s `_RETRY_COUNT_PENDING_PHASE_6` constant is
therefore still `0` — it counts decision-level retry cycles across
multiple diagnoses, a different thing from this within-action attempt
count, and no such re-diagnosis loop exists in the state machine to
drive it).
