"use server";

import type { Outcome } from "@/lib/api";
import { backendAuthHeaders } from "@/lib/backend-auth";

/**
 * Result of an observe-outcome attempt, returned to the client component
 * rather than thrown (mirrors decide-action.ts / action-action.ts).
 *
 * Adds no client-side idempotency logic of its own: a thin pass-through to
 * POST /recovery/cases/{id}/observe-outcome, itself idempotent -- see
 * app/outcome/service.py.
 */
export type OutcomeActionState =
  | { status: "idle" }
  | { status: "success"; outcome: Outcome }
  | { status: "error"; kind: "not_found" | "conflict" | "unavailable"; message: string };

const BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function observeOutcome(
  caseId: string,
  // Required by useActionState's action signature; this action needs no
  // previous state (it always re-runs the same idempotent POST).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _prevState: OutcomeActionState,
): Promise<OutcomeActionState> {
  try {
    const res = await fetch(`${BASE_URL}/recovery/cases/${caseId}/observe-outcome`, {
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
        message: "An outcome can only be observed once the action has executed.",
      };
    }
    if (!res.ok) {
      return {
        status: "error",
        kind: "unavailable",
        message: `The outcome observer did not respond as expected (HTTP ${res.status}).`,
      };
    }

    const outcome = (await res.json()) as Outcome;
    return { status: "success", outcome };
  } catch {
    return {
      status: "error",
      kind: "unavailable",
      message: "Could not reach the backend to observe this outcome.",
    };
  }
}
