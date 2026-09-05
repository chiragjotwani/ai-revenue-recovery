"use server";

import type { ManualReviewResolution } from "@/lib/api";
import { backendAuthHeaders } from "@/lib/backend-auth";

/**
 * Result of a resolve-manual-review attempt, returned to the client
 * component rather than thrown (mirrors decide-action.ts /
 * action-action.ts / outcome-action.ts).
 *
 * Unlike those actions, this one is NOT idempotent -- a case can only
 * leave `pending_manual_review` once (Phase 17), so a repeat submission
 * (e.g. a double click) surfaces as a `conflict`, the same "already
 * resolved" outcome a genuinely concurrent operator would also see.
 */
export type ManualReviewActionState =
  | { status: "idle" }
  | { status: "success"; resolution: ManualReviewResolution }
  | {
      status: "error";
      kind: "not_found" | "conflict" | "invalid" | "unavailable";
      message: string;
    };

const BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function resolveManualReview(
  caseId: string,
  _prevState: ManualReviewActionState,
  formData: FormData,
): Promise<ManualReviewActionState> {
  const resolution = String(formData.get("resolution") ?? "");
  const note = String(formData.get("note") ?? "");

  try {
    const res = await fetch(`${BASE_URL}/recovery/cases/${caseId}/resolve-manual-review`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...backendAuthHeaders() },
      body: JSON.stringify({ resolution, note }),
    });

    if (res.status === 404) {
      return { status: "error", kind: "not_found", message: "This case no longer exists." };
    }
    if (res.status === 409) {
      return {
        status: "error",
        kind: "conflict",
        message:
          "This case's manual review is not pending, or was already resolved by someone else.",
      };
    }
    if (res.status === 422) {
      return {
        status: "error",
        kind: "invalid",
        message: "A resolution and a non-empty note are both required.",
      };
    }
    if (!res.ok) {
      return {
        status: "error",
        kind: "unavailable",
        message: `The manual review could not be resolved as expected (HTTP ${res.status}).`,
      };
    }

    const resolutionOut = (await res.json()) as ManualReviewResolution;
    return { status: "success", resolution: resolutionOut };
  } catch {
    return {
      status: "error",
      kind: "unavailable",
      message: "Could not reach the backend to resolve this manual review.",
    };
  }
}
