"use server";

import type { Action } from "@/lib/api";

/**
 * Result of a schedule/execute-action attempt, returned to the client
 * component rather than thrown -- an operator hitting 409 (decision not
 * approved yet / wrong state) or a transient 5xx is a normal outcome to
 * show inline, not a page-crashing error (mirrors decide-action.ts).
 *
 * Adds no client-side idempotency logic of its own: both are thin pass-
 * throughs to the Phase 6 endpoints, which are themselves idempotent on
 * (case_id, action_type, decision_result_id) and (action_id, attempt_no)
 * respectively -- see app/decision/actions.py.
 */
export type ActionActionState =
  | { status: "idle" }
  | { status: "success"; action: Action }
  | { status: "error"; kind: "not_found" | "conflict" | "unavailable"; message: string };

const BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

async function postAction(caseId: string, path: "schedule-action" | "execute-action") {
  try {
    const res = await fetch(`${BASE_URL}/recovery/cases/${caseId}/${path}`, {
      method: "POST",
      cache: "no-store",
    });

    if (res.status === 404) {
      return { status: "error", kind: "not_found", message: "This case no longer exists." } as const;
    }
    if (res.status === 409) {
      return {
        status: "error",
        kind: "conflict",
        message:
          path === "schedule-action"
            ? "An action can only be scheduled for an approved decision on a case in decision_pending."
            : "An action can only be executed once it has been scheduled.",
      } as const;
    }
    if (!res.ok) {
      return {
        status: "error",
        kind: "unavailable",
        message: `The action executor did not respond as expected (HTTP ${res.status}).`,
      } as const;
    }

    const action = (await res.json()) as Action;
    return { status: "success", action } as const;
  } catch {
    return {
      status: "error",
      kind: "unavailable",
      message: "Could not reach the backend to perform this action.",
    } as const;
  }
}

export async function scheduleAction(
  caseId: string,
  // Required by useActionState's action signature; this action needs no
  // previous state (it always re-runs the same idempotent POST).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _prevState: ActionActionState,
): Promise<ActionActionState> {
  return postAction(caseId, "schedule-action");
}

export async function executeAction(
  caseId: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _prevState: ActionActionState,
): Promise<ActionActionState> {
  return postAction(caseId, "execute-action");
}
