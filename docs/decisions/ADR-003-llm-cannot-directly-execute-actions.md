# ADR-003: The LLM Can Never Directly Execute Financial Actions

## Context

This platform uses AI to diagnose payment failures and recommend recovery
strategies. LLMs can hallucinate facts, produce malformed output, or
recommend inappropriate actions (e.g. retrying a customer who already paid,
or exceeding a retry policy). If an LLM's output were wired directly to a
payment API, a hallucination becomes a real financial action.

## Decision

The LLM is never the final authority and is never wired directly to any
action executor. All AI output must pass through, in order:

```
Database -> Context Builder -> LLM -> Structured Output -> Schema Validation
-> Policy Engine -> Recovery State Validation -> Idempotency Validation
-> Action Executor
```

If any validation stage fails or evidence is insufficient, the outcome must
be `UNKNOWN` or a policy rejection — never a best-effort guess that
proceeds to execution.

## Alternatives Considered

- LLM directly calls a payment/action API ("agentic" tool-calling straight
  to production side effects): rejected outright as a safety-critical
  anti-pattern for this domain.
- Human approval for every action: not the default (would defeat the
  purpose of automation) but remains available as an escalation path for
  low-confidence or high-value cases (see Phase 5 decision engine).

## Reasoning

The database is the source of truth (ADR-001), not the LLM. The LLM may
never invent customer history, payment status, or prior actions. Structured
output schema validation, a deterministic policy engine, and idempotency
checks form a hard boundary between probabilistic reasoning and real-world
financial side effects.

## Consequences

- Every AI-influenced action must be traceable back through each
  validation stage in the audit log.
- New action types must be added to the policy engine before the LLM can
  ever recommend them meaningfully.
- This boundary must be tested explicitly (see the mandatory AI test cases
  in the project engineering prompt: hallucination, conflicting evidence,
  invalid JSON, forbidden action, duplicate action, recovered customer,
  high-value uncertain case).
