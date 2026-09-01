"use client";

import { useActionState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { observeOutcome, type OutcomeActionState } from "./outcome-action";
import type { Outcome } from "@/lib/api";
import { fmtTimestamp, outcomeSeverity } from "@/lib/api";
import { StatusPill } from "@/components/ui";

const initialState: OutcomeActionState = { status: "idle" };

const OUTCOME_COPY: Record<Outcome["outcome"], string> = {
  recovered: "Recovered — a later successful payment was observed for this customer.",
  not_recovered:
    "Not recovered — a later failed payment was observed, with no success since.",
  unresolved: "In progress. No conclusive payment evidence yet.",
};

/** The Phase 7 outcome panel: shows what actually happened, from
 * authoritative payment evidence alone -- never inferred from the fact
 * that an action executed. Replaces the earlier state-derived-only
 * "Outcome" panel content with the real observation record once one
 * exists, while keeping the same panel position/identity (no duplicate
 * outcome UI).
 *
 * Never implies revenue was recovered merely because Phase 6 executed an
 * action -- "executed" and "recovered" render as visibly distinct facts.
 */
export function OutcomePanel({
  caseId,
  caseState,
  actionStatus,
  initialOutcome,
  closedAt,
}: {
  caseId: string;
  caseState: string;
  actionStatus: string | null;
  initialOutcome: Outcome | null;
  closedAt: string | null;
}) {
  const observeWithId = observeOutcome.bind(null, caseId);
  const [state, formAction, pending] = useActionState(observeWithId, initialState);
  const router = useRouter();

  useEffect(() => {
    if (state.status === "success") {
      router.refresh();
    }
  }, [state, router]);

  const outcome = state.status === "success" ? state.outcome : initialOutcome;
  const canObserve =
    actionStatus === "executed" && (caseState === "action_executed" || caseState === "observing");

  return (
    <div className="flex flex-col gap-4 text-sm">
      {outcome ? (
        <OutcomeSummary outcome={outcome} />
      ) : caseState === "abandoned" || caseState === "failed" ? (
        <p className="text-sm text-critical">
          {caseState === "abandoned" ? "Abandoned" : "Failed"}
          {closedAt ? ` · closed ${fmtTimestamp(closedAt)}` : ""}
        </p>
      ) : (
        <p className="text-sm text-text-muted">
          In progress. No outcome yet — recovered revenue is measured in Phase 8.
        </p>
      )}

      {canObserve && (
        <form action={formAction} className="flex flex-col gap-3">
          <button
            type="submit"
            disabled={pending}
            aria-busy={pending}
            className="self-start border border-signal/50 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-signal hover:bg-signal/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Observing…" : outcome ? "Re-observe outcome" : "Observe outcome"}
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

function OutcomeSummary({ outcome }: { outcome: Outcome }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill
          label={outcome.outcome.replace(/_/g, " ")}
          severity={outcomeSeverity(outcome.outcome)}
        />
        {!outcome.is_terminal && (
          <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
            not yet terminal
          </span>
        )}
      </div>
      <p className="text-text-muted">{OUTCOME_COPY[outcome.outcome]}</p>
      <p className="font-mono text-[11px] text-text-dim">
        attempt {outcome.attempt_no}
        {outcome.evidence_payment_id ? ` · evidence: ${outcome.evidence_payment_id}` : ""} ·{" "}
        {fmtTimestamp(outcome.created_at)}
      </p>
    </div>
  );
}
