# Project State

## Current Phase

Phase 4 — AI Context & Diagnosis: COMPLETE (awaiting owner go-ahead before
Phase 5). Phases 0–2 remain frozen (`docs/phase-0-2-freeze.md`).

Owner decisions recorded:
- 2026-08-27: Phase 3 follows the frozen contract (Recovery Case
  Management state machine), not the ML-training interpretation. AI stays
  inference-only (ADR-003).
- 2026-08-28: Phase 4 diagnosis taxonomy is two layers (specific `outcome`
  + derived `disposition` — ADR-005). Qwen/Nemotron providers built as
  real config-gated OpenAI-compatible HTTP clients; a 6 GB-friendly local
  model path (Ollama `qwen3:4b`) is documented. GPU/model-selection
  decision (KI-002) still open — Phase 4 runs on the `mock` provider.

## Current Stage

N/A (Phase 4 closed; Phase 5 not started)

## Completed Phases

- Phase 0 — Engineering Foundation (frozen)
- Phase 1 — Data Foundation (frozen)
- Phase 2 — Revenue Risk Detection (frozen)
- Phase 3 — Recovery Case Management
- Phase 4 — AI Context & Diagnosis

## Completed Stages (Phase 4)

- Diagnosis schema (`backend/app/ai/schema.py`): `DiagnosisOutcome` (10
  specific causes incl. `unknown`), `DiagnosisDisposition` (4 routing
  categories, **derived from outcome by code**, not by the model),
  `RecoveryStrategy` (advisory), `ModelDiagnosisJSON` (the strict provider
  contract) and `Diagnosis` (validated + enriched). ADR-005.
- `RecoveryContextBuilder` (`backend/app/ai/context_builder.py`, Section
  49): bounded customer / payment / failure / capped-history / policies
  summary from Postgres only, plus derived `evidence_sufficiency` and
  `signals_conflict` signals. Never raw rows.
- Prompt versioning (`backend/app/ai/prompts.py`): `diagnosis_prompt_v1`,
  stored with every diagnosis (Section 50).
- `ReasoningModel` abstraction (`backend/app/ai/providers/`, Section 7):
  ABC + `MockProvider` (deterministic default; no model/network) +
  `QwenProvider` / `NemotronProvider` as real config-gated
  OpenAI-compatible HTTP clients + a factory that falls back to `mock`
  when a provider's base URL is unset.
- Structured-output validation + safeguards (`backend/app/ai/diagnosis.py`,
  Section 37): extract JSON → schema-validate → retry once → raise
  `DiagnosisValidationError` (never proceed); sparse context downgrades an
  over-confident answer to `unknown`; conflicting signals cap confidence.
- `diagnoses` table (`backend/app/models/diagnosis.py`, migration
  `2977e606c234`) storing outcome/disposition/confidence/reasoning/
  strategy + `model_name` / `model_version` / `prompt_version` /
  `schema_version` / `latency_ms` (Section 51). Strings not PG enums —
  ADR-005. Upgrade/downgrade roundtrip + `alembic check` clean.
- `diagnose_case` service + `POST /recovery/cases/{id}/diagnose`: build
  context → run provider → validate → persist → advance
  `detected → diagnosing → diagnosed`. `404` unknown / `409` wrong state /
  `502` unusable model output (case left in `diagnosing`, retryable,
  nothing persisted). `GET /recovery/cases/{id}` gains a `diagnosis` field.
  The AI path has no write access to payments and no action executor
  (ADR-003).
- Evaluation set (`backend/evaluation/diagnosis_cases.json`, 72 synthetic
  labelled cases; deterministic generator) + benchmark runner
  (`backend/scripts/benchmark_diagnosis.py`): outcome accuracy, schema
  compliance, hallucination rate, confidence-band adherence, latency,
  throughput (Section 52). KI-007 records that the numbers are agreement
  with synthetic labels, not real-world accuracy.
- Frontend: diagnosis panel on `/recovery/[id]`.
- ADR-005 added. KI-002 updated with the 6 GB GPU finding; KI-007 added.
- Tests: `test_ai_diagnosis.py` (22), `test_ai_providers.py` (11),
  `test_ai_context_builder.py` (4), `test_diagnosis_api.py` (5) — 42 new,
  **91 total passing**.

## Completed Stages (Phase 3)

- Models (`backend/app/models/recovery.py`): `RecoveryCase` (one per
  payment — `payment_id` unique), `RecoveryCaseTransition` (append-only
  history), `RecoveryCaseState` enum (full 10-state lifecycle from
  Section 16, defined now so later phases don't need enum migrations)
- Migration `e9bad135ac97`: `recovery_cases` + `recovery_case_transitions`
  + the `recovery_case_state` Postgres enum (created once, referenced with
  `create_type=False`); upgrade/downgrade roundtrip verified;
  `alembic check` shows no drift
- State machine (`backend/app/recovery/state_machine.py`): single
  `LEGAL_TRANSITIONS` map + `TERMINAL_STATES` + `INITIAL_STATE`; linear
  path plus terminal edges, no retry back-edge yet
- Transition service (`backend/app/recovery/service.py`): `open_case`
  (idempotent on payment, returns `(case, created)`, handles the
  concurrent-open race), `transition_case` (validates against the state
  machine, writes the history row + state change in one transaction,
  raises `IllegalStateTransitionError`, sets `closed_at` on terminal),
  plus `get_case` / `get_case_transitions` / `list_cases`
- API (`backend/app/api/recovery.py`, `docs/api/recovery.md`):
  `POST /recovery/cases` (201 new / 200 existing / 404 unknown payment /
  409 not-failed), `GET /recovery/cases?state=`,
  `GET /recovery/cases/{id}` (with ordered history),
  `POST /recovery/cases/{id}/transitions` (200 / 404 / 409 illegal /
  422 bad state)
- Frontend: `/recovery` (case list, state badges) and `/recovery/[id]`
  (case detail + transition timeline), SSR, same style as `/risk`; linked
  from the risk dashboard
- Tests: `test_recovery_state_machine.py` (17 pure tests: every state
  mapped, terminals have no exits, no self-loops, full happy path legal,
  8 parametrized illegal transitions raise) and `test_recovery_api.py`
  (10 integration tests vs. real Postgres: open/idempotent-open/404/409,
  legal & illegal transitions via API, terminal sets `closed_at` and
  freezes, list + state filter, chronological history) — 27 new tests,
  **49 total passing**
- ADR-004 added (explicit recovery state machine + append-only transition
  log)
- Full verification: 49/49 pytest, ruff/format/mypy strict clean, frontend
  eslint/tsc/build clean, `alembic upgrade`/`downgrade`/`check` clean,
  verified end-to-end in a from-clean Docker rebuild

## Completed Stages (Phase 2)

- Risk features (`backend/app/risk/features.py`): consecutive failures
  since a customer's last success, historical success rate, computed
  from Postgres only
- Rule-based deterministic scoring (`backend/app/risk/scoring.py`):
  weighted combination of consecutive failures, failure-reason severity,
  and historical unreliability into a `[0, 1]` score with low/medium/high
  buckets. No ML/LLM involved at this phase.
- Revenue-at-risk detection rule: a failed payment with no later
  successful payment for the same customer (`backend/app/risk/service.py`)
- Risk API: `GET /risk/payments`, `GET /risk/summary`
  (`docs/api/risk.md`)
- Risk dashboard: frontend route `/risk` (stat tiles + at-risk payments
  table, status-colored risk-level badges with text labels, not
  color-only)
- Tests: `test_risk_scoring.py` (pure-function unit tests: weight sum,
  bounds, monotonicity, bucket edges), `test_risk_api.py` (integration
  against real Postgres: no-history case, superseded-failure exclusion,
  consecutive-failure counting, summary aggregation, empty case) — 11 new
  tests, 22 total passing
- Fixed a real pre-existing type bug from Phase 1 caught by mypy while
  building this phase: `Payment.amount` was annotated `Mapped[str]` for a
  `Numeric` column that actually yields `Decimal` at runtime
- Full verification: 22/22 tests pass, ruff/format/mypy clean, frontend
  tsc/lint/build clean, verified end-to-end in freshly rebuilt Docker
  containers (ingested the canonical scenario through the container
  network, confirmed risk score/level match hand-computed expected
  values, dashboard rendered live data via SSR)
- Documentation: `docs/api/risk.md`, `docs/architecture.md` updated,
  known-issues updated (KI-006: documented, not fixed — no FX source
  exists to sum multiple currencies correctly, not required yet)

## Known Issues

See `docs/known-issues.md`.
- Open: KI-002 (self-hosted model infra undecided, relevant Phase 4+),
  KI-006 (revenue_at_risk has no FX conversion across currencies —
  documented limitation, not a defect against any current requirement)
- Resolved: KI-001, KI-003, KI-004, KI-005
- Phase 3 non-blocking notes (not defects): the concurrent-open race
  branch in `open_case` and the `seed_synthetic_data.py` script still have
  no dedicated automated test (carried over from Phase 1); no frontend
  test runner exists yet, so `/recovery` pages are verified by rendering
  SSR HTML.
- Phase 4: KI-007 added (synthetic evaluation set; benchmark accuracy is
  label-agreement, not real-world). KI-002 updated (6 GB GPU can't host
  the 30B candidates; `mock` provider is the Phase 4 default).

## Architecture Decisions

- ADR-001: PostgreSQL as source of truth
- ADR-002: Modular monolith
- ADR-003: LLM cannot directly execute financial actions
- ADR-004: Explicit recovery state machine with an append-only transition log
- ADR-005: Diagnosis output — two layers, derived disposition, everything versioned

## Last Successful Verification

2026-08-28 Phase 4 gate. Backend: **pytest 91/91**, ruff check, ruff
format --check, mypy strict (46 files), `alembic upgrade head` from clean
(3 migrations), `alembic check` (no drift), `alembic downgrade -1`/
`upgrade` roundtrip on the diagnoses migration — all clean. Frontend:
eslint, `next typegen` from clean, `tsc --noEmit`, `next build` (routes
`/`, `/risk`, `/recovery`, `/recovery/[id]`) — all clean.
`benchmark_diagnosis.py --provider mock` runs over 72 eval cases (all
metrics 1.0 by construction — KI-007). Full stack rebuilt from clean
(`docker compose down -v && up --build`): all 3 migrations auto-applied on
backend container start; canonical scenario seeded; case opened, then
`POST /recovery/cases/{id}/diagnose` → `200`
`insufficient_funds`/`retriable_transient`/conf 0.9/**retry +6h** (matches
Section 38), case advanced `detected → diagnosing → diagnosed`, re-diagnose
→ `409`; `/recovery/[id]` rendered the diagnosis panel via SSR across the
container network. Phase 0–3 regression still green within the 91.

The Phase 0–2 freeze gate detail remains in `docs/phase-0-2-freeze.md`.

## Last Git Commit

`phase-4: implement AI recovery diagnosis` — see `git log`.
(Preceded by `phase-3: …` and `freeze: phases 0-2 verified`.)

## Process Note

Per explicit agreement with the project owner: phase-boundary check-ins
are mandatory (a structured completion report is posted and approval is
awaited before starting the next phase), and any implementation decision
carrying even slight uncertainty is raised to the project owner for
approval before proceeding.
