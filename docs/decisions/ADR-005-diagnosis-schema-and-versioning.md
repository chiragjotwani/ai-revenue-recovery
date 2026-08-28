# ADR-005: Diagnosis Output — Two Layers, Derived Disposition, Everything Versioned

## Context

Phase 4 has the reasoning model diagnose *why* a payment failed. Phase 5's
decision engine will branch on that diagnosis. Sections 50 and 51 require
that every production prompt is versioned and that model/prompt/schema
metadata is stored with every AI decision so versions can be compared
later.

Two forces pull in different directions: Phase 5 wants a small, stable set
of routing categories to branch on; learning (Phase 9) and humans want the
specific cause preserved.

## Decision

1. **Two layers.**
   - `DiagnosisOutcome` — the specific cause (`insufficient_funds`,
     `card_expired`, `fraud_suspected`, … , `unknown`). This is what the
     model chooses.
   - `DiagnosisDisposition` — the coarse routing category
     (`retriable_transient`, `customer_action_required`, `suspected_fraud`,
     `unknown`). This is what Phase 5 branches on.

2. **The disposition is derived by our code, never by the model.**
   `app/ai/schema.py::_OUTCOME_DISPOSITION` maps each outcome to exactly
   one disposition. The model returns only the outcome; we compute the
   disposition. A model therefore cannot emit a cause/disposition pair
   that disagrees, and Phase 5's routing surface stays small and stable
   even as the cause taxonomy grows.

3. **`unknown` is a first-class, expected outcome.** Insufficient evidence
   resolves to `unknown`, not a guess (Section 37). A cheap safeguard
   downgrades an over-confident specific answer to `unknown` when the
   context builder flagged the evidence as sparse, and caps confidence
   when it flagged conflicting signals.

4. **A recommended strategy is carried, but it is advisory only.**
   `recommended_strategy` / `recommended_delay_hours` exist because
   Section 38 has the AI recommending "a retry after 6 hours". Phase 5's
   policy engine is the authority; nothing in Phase 4 executes anything
   (ADR-003).

5. **Versioning.** Every stored diagnosis records `model_name`,
   `model_version`, `prompt_version` (`diagnosis_prompt_v1`),
   `schema_version` (`"1"`), and `latency_ms`. Prompts are constants in
   `app/ai/prompts.py`; a change means a new `diagnosis_prompt_vN`, never
   an in-place edit.

6. **Diagnosis enum values are stored as strings, not Postgres enums.**
   The cause taxonomy is expected to move as models and evaluation data
   mature; app-level validation (`ModelDiagnosisJSON`) already guarantees
   the values, and string columns avoid an `ALTER TYPE` migration every
   time the taxonomy changes. (Contrast `recovery_case_state`, a lifecycle
   that is deliberately fixed — ADR-004.)

## Alternatives Considered

- **Ask the model for the disposition directly.** Rejected: invites
  cause/disposition disagreement and makes the routing surface depend on
  model behaviour.
- **Only a disposition, no specific cause.** Rejected: discards the signal
  Phase 9 learning and human reviewers need.
- **Postgres enums for the diagnosis taxonomy.** Rejected for churn (see
  decision 6).

## Consequences

- Phase 5 reads `disposition` (and may read `confidence` and
  `recommended_strategy`); it does not need to know every cause value.
- Adding a cause = add the enum member + one line in `_OUTCOME_DISPOSITION`
  + regenerate/extend the evaluation set. No migration.
- The evaluation set (`backend/evaluation/diagnosis_cases.json`) and the
  benchmark (`backend/scripts/benchmark_diagnosis.py`) are the mechanism
  for comparing model/prompt versions over time (Section 52). Its accuracy
  numbers are agreement with synthetic labels — see KI-007.
