# AI Recovery Diagnosis (Phase 4)

The reasoning model looks at a **bounded, curated context** for one
recovery case and returns a **structured diagnosis** — the likely cause of
the payment failure, a confidence, a short rationale, and an *advisory*
recovery strategy. It does not decide and it does not act (ADR-003); those
are Phases 5 and 6.

## Pipeline

```
RecoveryCase
  -> RecoveryContextBuilder        (app/ai/context_builder.py, Section 49)
  -> ReasoningModel provider       (app/ai/providers/, Section 7)
  -> extract JSON + schema validate (app/ai/diagnosis.py, Section 37)
  -> safeguards                     (sparse -> UNKNOWN; conflict -> cap confidence)
  -> persist Diagnosis row          (Section 51)
  -> transition case DETECTED -> DIAGNOSING -> DIAGNOSED
```

If the model is unreachable or its output cannot be validated after a
retry, the case is left in `diagnosing`, nothing is persisted, and the API
returns `502`. The diagnosis can be retried from `diagnosing`.

## The context (what the model sees)

`RecoveryContextBuilder` assembles, from Postgres only:

- **customer summary** — external id, tenure (days), total/successful
  payments, historical success rate
- **payment summary** — reference, amount, currency, status, failure
  reason, timestamp
- **failure summary** — consecutive failures since last success, distinct
  prior failure reasons, days since last success
- **recent history** — up to the last 10 non-pending payments (the failing
  one excluded)
- **previous interventions** — empty until Phase 6
- **applicable policies** — a static list today; the real policy store is
  Phase 5
- **derived signals** — `evidence_sufficiency` (`sufficient` | `sparse`)
  and `signals_conflict`

It never sends raw rows or unbounded history.

## The diagnosis (what comes back)

Two layers (ADR-005):

- `outcome` — the specific cause the model chose. One of:
  `insufficient_funds`, `card_expired`, `do_not_honor`, `processing_error`,
  `stolen_card`, `lost_card`, `fraud_suspected`, `authentication_required`,
  `card_not_supported`, `unknown`.
- `disposition` — the routing category, **derived by our code** from the
  outcome: `retriable_transient`, `customer_action_required`,
  `suspected_fraud`, `unknown`. Phase 5 branches on this.

Plus `confidence` (0–1), `reasoning`, `recommended_strategy` +
`recommended_delay_hours` (advisory only), and the audit fields
`model_name`, `model_version`, `prompt_version`, `schema_version`,
`latency_ms`.

## Providers

`REASONING_PROVIDER` selects the implementation:

- **`mock`** (default) — `app/ai/providers/mock.py`. Deterministic, derives
  a diagnosis from the context. No network, no model. Used by dev, CI, and
  every test.
- **`qwen`** / **`nemotron`** — OpenAI-compatible HTTP clients
  (`app/ai/providers/openai_compatible.py`). Point `AI_QWEN_BASE_URL` /
  `AI_NEMOTRON_BASE_URL` at any server that speaks
  `POST {base_url}/chat/completions` (Ollama, llama.cpp, vLLM, LM Studio,
  or a hosted endpoint). If the selected provider's base URL is unset, the
  app falls back to `mock`. See `local-model-setup.md`.

The contract candidates are Qwen3-30B-A3B-Instruct-2507 and
Nemotron 3 Nano 30B-A3B (Section 7); the choice between them is deferred to
the benchmark once model infrastructure is decided (KI-002).

## Evaluation & benchmark (Section 52)

- `backend/evaluation/diagnosis_cases.json` — a fixed set of ~70 synthetic,
  labelled cases (regenerate with `evaluation/generate_diagnosis_cases.py`;
  the JSON is the artifact of record). Do **not** edit it to improve a
  score (KI-007).
- `python backend/scripts/benchmark_diagnosis.py --provider <name>` — runs
  a provider over the set and reports outcome accuracy, schema-compliance
  rate, hallucination rate, confidence-band adherence, latency, and
  throughput. VRAM/memory is measured out of process.

Against `mock` every metric is 1.0 by construction (the mock and the
labels share the same reason→outcome logic); the run still proves the
pipeline end-to-end and the harness is ready for a real model.

## Mandatory AI test cases covered now (Section 37)

- incomplete context → `unknown` (`test_ai_diagnosis.py`)
- conflicting evidence → low confidence
- invalid JSON → validation failure, case not advanced, nothing persisted

Forbidden-action, duplicate-action, recovered-customer, and
high-value-escalation cases belong to the Phase 5 decision engine and
Phase 6 action executor and are added there.

## API

See `docs/api/recovery.md` — `POST /recovery/cases/{id}/diagnose` and the
`diagnosis` field on `GET /recovery/cases/{id}`.
