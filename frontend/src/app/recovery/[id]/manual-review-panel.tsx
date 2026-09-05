"use client";

import { useActionState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  resolveManualReview,
  type ManualReviewActionState,
} from "./manual-review-action";
import type { ManualReviewResolution } from "@/lib/api";
import { fmtTimestamp } from "@/lib/api";
import { StatusPill } from "@/components/ui";

const initialState: ManualReviewActionState = { status: "idle" };

/** Phase 17: an operator resolves a case escalated to `manual_review`
 * (Phase 5 policy engine: fraud suspicion, insufficient evidence,
 * conflicting signals). Shown only while the case is
 * `pending_manual_review` and unresolved -- this is the only way such a
 * case can ever leave that state; there is no automated re-decision loop
 * and no automatic timeout.
 *
 * Deliberately offers only two resolutions (abandoned / failed) --
 * never "recovered": no authoritative payment evidence exists merely
 * because a human looked at the case, and this panel does not, and
 * cannot, fabricate one.
 */
export function ManualReviewPanel({
  caseId,
  caseState,
  initialResolution,
}: {
  caseId: string;
  caseState: string;
  initialResolution: ManualReviewResolution | null;
}) {
  const resolveWithId = resolveManualReview.bind(null, caseId);
  const [state, formAction, pending] = useActionState(resolveWithId, initialState);
  const router = useRouter();

  useEffect(() => {
    if (state.status === "success") {
      router.refresh();
    }
  }, [state, router]);

  const resolution = state.status === "success" ? state.resolution : initialResolution;
  const canResolve = caseState === "pending_manual_review" && resolution === null;

  if (!canResolve && resolution === null) {
    return (
      <p className="text-sm text-text-muted">
        This case has not been escalated to manual review.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4 text-sm">
      {resolution ? (
        <ResolutionSummary resolution={resolution} />
      ) : (
        <p className="border border-signal/30 bg-signal/[0.06] px-3 py-2.5 text-text">
          The policy engine escalated this case for manual review. An operator must resolve it
          below — there is no automated re-decision and no timeout.
        </p>
      )}

      {canResolve && (
        <form action={formAction} className="flex flex-col gap-3">
          <fieldset className="flex flex-col gap-2">
            <legend className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
              Resolution
            </legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="resolution" value="abandoned" required />
              <span>Abandon — stop pursuing this case</span>
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="resolution" value="failed" required />
              <span>Mark failed — the recovery attempt did not work</span>
            </label>
          </fieldset>
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
              Note (required)
            </span>
            <textarea
              name="note"
              required
              minLength={1}
              maxLength={1000}
              rows={3}
              className="border border-border bg-bg-inset px-2 py-1.5 text-sm text-text"
              placeholder="Why this case is being resolved this way — recorded permanently."
            />
          </label>
          <button
            type="submit"
            disabled={pending}
            aria-busy={pending}
            className="self-start border border-signal/50 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-signal hover:bg-signal/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Resolving…" : "Resolve manual review"}
          </button>
        </form>
      )}

      {state.status === "error" && (
        <div
          role="alert"
          className="border border-critical/40 bg-critical/[0.06] px-3 py-2 text-xs text-critical"
        >
          {state.message}
        </div>
      )}
    </div>
  );
}

function ResolutionSummary({ resolution }: { resolution: ManualReviewResolution }) {
  return (
    <div className="flex flex-col gap-3">
      <StatusPill
        label={resolution.resolution.replace(/_/g, " ")}
        severity={resolution.resolution === "abandoned" ? "neutral" : "critical"}
      />
      <p className="text-text-muted">{resolution.note}</p>
      <p className="font-mono text-[11px] text-text-dim">
        by {resolution.actor} · {fmtTimestamp(resolution.created_at)}
      </p>
    </div>
  );
}
