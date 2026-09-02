/**
 * Server-side API client. Every call runs in a Server Component, so the
 * base URL is the server-only `API_BASE_URL` (never `NEXT_PUBLIC_`, which
 * Next inlines at build time and breaks in Docker -- KI-001).
 *
 * `get` returns a discriminated result so callers can tell "the API is
 * down" apart from "that record does not exist" (BUG-004): 404/400/422 ->
 * `not_found`, transport/5xx -> `unavailable`.
 */

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; kind: "not_found" }
  | { ok: false; kind: "unavailable" };

const BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
    if (res.status === 404 || res.status === 400 || res.status === 422) {
      return { ok: false, kind: "not_found" };
    }
    if (!res.ok) return { ok: false, kind: "unavailable" };
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, kind: "unavailable" };
  }
}

// ---- shared response types (mirror backend/app/schemas) ----

export type Health = { status: string; environment: string };

export type RiskSummary = {
  at_risk_payment_count: number;
  revenue_at_risk: string;
  currency_breakdown: Record<string, string>;
  risk_level_breakdown: { low: number; medium: number; high: number };
};

export type RiskAssessment = {
  payment_id: string;
  customer_id: string;
  external_reference: string;
  amount: string;
  currency: string;
  failure_reason: string | null;
  consecutive_failures: number;
  historical_success_rate: number;
  risk_score: number;
  risk_level: "low" | "medium" | "high";
};

export type RecoveryCase = {
  id: string;
  payment_id: string;
  customer_id: string;
  state: string;
  opened_at: string;
  closed_at: string | null;
};

export type Diagnosis = {
  outcome: string;
  disposition: string;
  confidence: number;
  reasoning: string;
  recommended_strategy: string;
  recommended_delay_hours: number | null;
  model_name: string;
  model_version: string;
  prompt_version: string;
  schema_version: string;
  latency_ms: number;
  created_at: string;
};

export type Transition = {
  id: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
  actor: string;
  created_at: string;
};

export type DecisionRationaleEntry = {
  rule_id: string;
  outcome: "passed" | "failed" | "not_applicable";
  reason_code: string | null;
};

export type DecisionStatus = "approved" | "rejected" | "escalated" | "superseded";

export type Decision = {
  id: string;
  case_id: string;
  diagnosis_id: string;
  recoverability: string;
  candidate_strategy: string;
  approved_strategy: string;
  decision_status: DecisionStatus;
  rationale: DecisionRationaleEntry[];
  scheduled_not_before: string | null;
  decision_engine_version: string;
  created_at: string;
};

export type ActionExecution = {
  id: string;
  attempt_no: number;
  idempotency_key: string;
  outcome: string;
  created_at: string;
};

export type Action = {
  id: string;
  case_id: string;
  decision_result_id: string;
  action_type: string;
  status: "scheduled" | "executed" | "failed";
  created_at: string;
  executions: ActionExecution[];
};

export type ObservedOutcome = "recovered" | "not_recovered" | "unresolved";

export type Outcome = {
  id: string;
  case_id: string;
  action_id: string;
  attempt_no: number;
  outcome: ObservedOutcome;
  is_terminal: boolean;
  evidence_payment_id: string | null;
  created_at: string;
};

export type Measurement = {
  id: string;
  case_id: string;
  payment_id: string;
  outcome_observation_id: string;
  status: ObservedOutcome;
  measured_at: string;
};

export type RecoveryCaseDetail = RecoveryCase & {
  history: Transition[];
  diagnosis: Diagnosis | null;
  decision: Decision | null;
  action: Action | null;
  outcome: Outcome | null;
  measurement: Measurement | null;
};

// ---- Phase 8: revenue measurement report ----

export type CurrencyAmount = { currency: string; amount: string; case_count: number };
export type BreakdownEntry = {
  key: string;
  currency: string;
  amount: string;
  case_count: number;
};

/** Every number here is OBSERVED (a later successful/failed payment exists
 * as evidence), never a causal or incremental estimate --
 * `counterfactual_available` is always `false` today: this system has no
 * randomized control group or untreated cohort to compute one from.
 */
export type RevenueReport = {
  measurement_basis: "observed_evidence";
  counterfactual_available: false;
  counterfactual_limitation: string;
  eligible_case_count: number;
  eligible_at_risk: CurrencyAmount[];
  observed_recovered: CurrencyAmount[];
  observed_not_recovered: CurrencyAmount[];
  unresolved: CurrencyAmount[];
  recovered_case_count: number;
  observed_recovery_rate: number;
  recovered_by_strategy: BreakdownEntry[];
  recovered_by_disposition: BreakdownEntry[];
};

// ---- Phase 9: strategy analytics (scoped -- no ML/probability/optimization) ----

export type StrategyStat = {
  key: string;
  total_case_count: number;
  observed_count: number;
  recovered_count: number;
  not_recovered_count: number;
  unresolved_count: number;
  empirical_recovery_rate: number | null;
  low_sample: boolean;
};

/** Every rate here is an observed frequency over a disclosed sample size --
 * never a prediction. `ml_model_status` is always "not_implemented": this
 * system has no real-world outcome data at a volume that could train or
 * validate one (KI-007).
 */
export type StrategyAnalyticsReport = {
  dataset_size: number;
  low_sample_threshold: number;
  by_strategy: StrategyStat[];
  by_disposition: StrategyStat[];
  ml_model_status: "not_implemented";
  ml_model_limitation: string;
};

// ---- Phase 10: model router / real model usage report ----

/** What the model router requested vs. resolved -- a config-time
 * substitution (e.g. AI_QWEN_BASE_URL unset) is explicit here, not
 * discoverable only after a diagnosis completes (KI-009).
 */
export type ProviderStatus = {
  requested_provider: string;
  resolved_provider: string;
  substituted: boolean;
  substitution_reason: string | null;
};

/** Real, recorded usage for one model -- never synthetic evaluation data
 * (KI-007). `escalation_count` is transport-failure fallbacks only,
 * never confidence-based (this project does not trust that number).
 */
export type ModelReportEntry = {
  model_name: string;
  diagnosis_count: number;
  mean_latency_ms: number;
  mean_confidence: number;
  escalation_count: number;
};

export type ModelReport = {
  router: ProviderStatus;
  by_model: ModelReportEntry[];
};

// ---- domain helpers ----

export const TERMINAL_STATES = new Set(["recovered", "abandoned", "failed"]);
export const OPEN_STATES = new Set([
  "detected",
  "diagnosing",
  "diagnosed",
  "decision_pending",
  "action_scheduled",
  "action_executed",
  "observing",
]);

export function riskSeverity(level: string): "good" | "warn" | "critical" {
  return level === "high" ? "critical" : level === "medium" ? "warn" : "good";
}

export function decisionSeverity(status: DecisionStatus): "good" | "warn" | "critical" | "neutral" {
  if (status === "approved") return "good";
  if (status === "escalated") return "warn";
  if (status === "rejected") return "critical";
  return "neutral"; // superseded
}

export function actionStatusSeverity(status: string): "good" | "warn" | "critical" | "neutral" {
  if (status === "executed") return "good";
  if (status === "failed") return "critical";
  return "neutral"; // scheduled
}

export function outcomeSeverity(outcome: ObservedOutcome): "good" | "warn" | "critical" | "neutral" {
  if (outcome === "recovered") return "good";
  if (outcome === "not_recovered") return "warn";
  return "neutral"; // unresolved
}

export function caseSeverity(state: string): "good" | "warn" | "critical" | "neutral" {
  if (state === "recovered") return "good";
  if (state === "abandoned" || state === "failed") return "critical";
  if (OPEN_STATES.has(state)) return "warn";
  return "neutral";
}

/** A provenance label distinguishing a real model from the deterministic mock. */
export function provenanceLabel(modelName: string): {
  text: string;
  severity: "signal" | "neutral";
  isReal: boolean;
} {
  if (modelName === "mock") {
    return { text: "Mock diagnosis (deterministic, no model)", severity: "neutral", isReal: false };
  }
  return { text: `Model: ${modelName}`, severity: "signal", isReal: true };
}

export function formatCurrencyMap(breakdown: Record<string, string>): string {
  const entries = Object.entries(breakdown);
  if (entries.length === 0) return "0";
  return entries.map(([cur, amt]) => `${amt} ${cur}`).join("  +  ");
}

/** Renders a list of per-currency amounts (Phase 8) exactly as returned --
 * never summed together (KI-006: no FX/cross-currency conversion source
 * exists). "0" for an empty list, never a fabricated combined total.
 */
export function formatCurrencyAmounts(amounts: CurrencyAmount[]): string {
  if (amounts.length === 0) return "0";
  return amounts.map((a) => `${a.amount} ${a.currency}`).join("  +  ");
}

export function fmtTimestamp(ts: string): string {
  return new Date(ts).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}
