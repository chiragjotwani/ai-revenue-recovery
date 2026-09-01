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

export type RecoveryCaseDetail = RecoveryCase & {
  history: Transition[];
  diagnosis: Diagnosis | null;
  decision: Decision | null;
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

export function fmtTimestamp(ts: string): string {
  return new Date(ts).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}
