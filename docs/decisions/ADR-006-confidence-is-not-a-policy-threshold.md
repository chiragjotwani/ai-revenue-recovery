# ADR-006: Model Confidence Is Not a Deterministic Policy Threshold

## Context

The project's governing prompt (`docs/master-loop-engineering-prompt.md`,
Section 24) lists "confidence thresholds" and "escalation logic" as
required Phase 5 deliverables, and ADR-003 anticipates "an escalation path
for low-confidence or high-value cases" as Phase 5's job. The Section-37
safety contract `test_contract_high_value_low_confidence_escalates_to_manual_review`
(added in Phase 4.1, `backend/tests/test_recovery_safety_contracts.py`)
encoded this literally: escalate on low model-reported confidence.

Phase 4's own schema explicitly documents why a raw numeric threshold on
that field is unsafe: `ModelDiagnosisJSON.confidence`
(`backend/app/ai/schema.py`) states plainly that this value is "not a
calibrated probability of correctness -- real models tend to report ~1.0
for any clear-looking reason code," and that neither the Phase 4
safeguards nor "the Phase 5 policy engine" should rely on it being
well-calibrated. No evaluation methodology exists (KI-007) that would
establish what any particular confidence value statistically means. A
numeric cutoff (e.g. "escalate if confidence < 0.3") would therefore be
gameable, meaningless noise dressed up as a safety control -- worse than
no rule at all, because it would look like a safeguard without being one.

The governing prompt's own preamble requires that any deviation from its
methodology be recorded as a new ADR rather than decided silently. Phase
5's Architecture Revision dropped the confidence-threshold requirement
without doing so. This ADR is that record.

## Decision

1. **Raw model-reported confidence (`ModelDiagnosisJSON.confidence` /
   `Diagnosis.confidence`) is never used as a deterministic Phase 5 policy
   input.** `app/decision/policy.py::PolicyInput` has no confidence field
   at all -- this is enforced structurally (the type cannot carry it), not
   just by convention.

2. **The project's deterministic proxy for "the model wasn't confident
   enough to trust" is the pair of typed, database-derived signals the
   context builder already computes:**
   - `evidence_sufficiency = "sparse"` (too little payment history, or no
     failure reason at all)
   - `signals_conflict = true` (e.g. a fraud-adjacent failure reason on an
     otherwise reliable, established customer)

   Both are computed in `app/ai/context_builder.py` from real database
   rows, not from the model's self-assessment, and both are already
   validated safeguards in Phase 4 (they downgrade an over-confident
   answer to `unknown` and cap confidence on conflicting signals — ADR-005
   decision 3). Phase 5B's policy engine escalates to `manual_review` on
   either signal, unconditionally, before any strategy-specific rule runs
   (`app/decision/policy.py`, Rule 4).

3. **This satisfies ADR-003's and the governing prompt's intent — an
   escalation path exists for cases the system isn't confident about — via
   a mechanism grounded in real data, not an arbitrary number on an
   admittedly uncalibrated self-report.** It is not a weaker
   interpretation of the requirement; it is a more defensible one.

4. **High-value escalation remains deferred, not implemented, and not
   silently dropped.** No canonical "high value" concept exists anywhere
   in the repository (no amount threshold, no currency-normalization
   logic), and KI-006 (`docs/known-issues.md`) remains unresolved for
   cross-currency amount aggregation. Implementing a threshold now would
   mean fabricating both the currency basis and the number — exactly what
   this project's engineering discipline forbids. This is pinned as an
   explicit `xfail` contract
   (`test_contract_high_value_escalates_to_manual_review`) documented as
   blocked on an owner decision, not deleted.

## Alternatives Considered

- **Add a numeric confidence threshold as originally listed.** Rejected:
  contradicts Phase 4's own documented understanding of what the field
  means: a threshold on an uncalibrated, model-self-reported number is not
  a safety control, it is a false sense of one.
- **Ignore the governing prompt's requirement entirely, treat it as
  superseded.** Rejected: the governing prompt explicitly requires
  deviations to be recorded as an ADR, not silently dropped — this
  decision honors that process by recording the substitution instead of
  pretending the original requirement never existed.
- **Implement a high-value threshold using an arbitrary round number
  (e.g. $1000) to unblock the test.** Rejected outright: this is exactly
  the fabrication this project's engineering discipline (Section 44/45,
  KI-006's own resolution notes) forbids, and it would misrepresent
  cross-currency amounts KI-006 already flags as not safely comparable.

## Reasoning

Deterministic policy safety should rest on facts the system actually
knows (payment/customer history, a computed evidence signal) rather than
on numbers a probabilistic model reports about itself. Phase 4 already
built exactly the right typed signals for this purpose as part of its own
hallucination safeguards; Phase 5 reusing them, rather than inventing a
new number, keeps the diagnosis-to-decision boundary honest about what
each side actually contributes (ADR-003's pipeline).

## Consequences

- `app/decision/policy.py` has no confidence field anywhere in its input
  type; a future contributor cannot accidentally wire the raw confidence
  float into a policy decision without first removing this ADR's
  reasoning and this codebase's structural guard.
- The Section-37 contract that originally bundled "high-value" and "low
  confidence" into one test has been split into
  `test_contract_insufficient_or_conflicting_evidence_escalates_to_manual_review`
  (now a real, passing assertion) and
  `test_contract_high_value_escalates_to_manual_review` (still `xfail`,
  explicitly documented as owner-decision-pending).
- KI-006 is not resolved by this ADR and must not be considered resolved
  until a currency basis and threshold are owner-approved.
- If real-world outcome data (Phase 7/8) ever demonstrates that model
  confidence *is* meaningfully predictive once calibrated, that would be a
  new, separate, evidence-backed decision — not a retroactive
  reinterpretation of this one.
