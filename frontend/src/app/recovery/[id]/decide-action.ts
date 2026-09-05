"use server";

import type { Decision } from "@/lib/api";
import { backendAuthHeaders } from "@/lib/backend-auth";

/**
 * Result of a decide attempt, returned to the client component rather than
 * thrown -- an operator hitting 409 (not yet diagnosed) or a transient 5xx
 * is a normal outcome to show inline, not a page-crashing error (unlike
 * openRecoveryCase in app/risk/actions.ts, which throws because that
 * action's own failure modes are all genuinely exceptional there).
 *
 * This adds no client-side idempotency logic of its own: it is a thin
 * pass-through to POST /recovery/cases/{id}/decide, which is itself
 * idempotent on (case_id, diagnosis_id) -- see app/decision/service.py.
 */
export type DecideActionState =
  | { status: "idle" }
  | { status: "success"; decision: Decision }
  | { status: "error"; kind: "not_found" | "conflict" | "invalid" | "unavailable"; message: string };

const BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function decideCase(
  caseId: string,
  // Required by useActionState's action signature; this action needs no
  // previous state (it always re-runs the same idempotent POST).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _prevState: DecideActionState,
): Promise<DecideActionState> {
  try {
    const res = await fetch(`${BASE_URL}/recovery/cases/${caseId}/decide`, {
      method: "POST",
      cache: "no-store",
      headers: backendAuthHeaders(),
    });

    if (res.status === 404) {
      return { status: "error", kind: "not_found", message: "This case no longer exists." };
    }
    if (res.status === 409) {
      return {
        status: "error",
        kind: "conflict",
        message: "This case is not in a decidable state (it may not be diagnosed yet).",
      };
    }
    if (res.status === 422) {
      return { status: "error", kind: "invalid", message: "The case id was invalid." };
    }
    if (!res.ok) {
      return {
        status: "error",
        kind: "unavailable",
        message: `The decision engine did not respond as expected (HTTP ${res.status}).`,
      };
    }

    const decision = (await res.json()) as Decision;
    return { status: "success", decision };
  } catch {
    return {
      status: "error",
      kind: "unavailable",
      message: "Could not reach the backend to make a decision.",
    };
  }
}
