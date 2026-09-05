"use server";

import { redirect } from "next/navigation";

import { backendAuthHeaders } from "@/lib/backend-auth";

/**
 * Open (or find the existing) recovery case for an at-risk payment, then
 * go to it. `POST /recovery/cases` is idempotent on `payment_id` (201 for
 * a genuinely new case, 200 for one that already exists), so this action
 * is always safe to invoke -- it never creates a duplicate.
 *
 * This is a real, implemented capability. It does NOT execute any recovery
 * action; it only opens the case file (ADR-003).
 */
export async function openRecoveryCase(formData: FormData): Promise<void> {
  const paymentId = String(formData.get("payment_id") ?? "");
  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

  const res = await fetch(`${baseUrl}/recovery/cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...backendAuthHeaders() },
    body: JSON.stringify({ payment_id: paymentId }),
    cache: "no-store",
  });

  if (!res.ok) {
    throw new Error(`Could not open a recovery case for ${paymentId} (HTTP ${res.status}).`);
  }

  const data = (await res.json()) as { id: string };
  redirect(`/recovery/${data.id}`);
}
