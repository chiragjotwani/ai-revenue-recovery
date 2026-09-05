"use client";

import { useActionState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { decideCase, type DecideActionState } from "./decide-action";
import type { Decision } from "@/lib/api";
import { decisionSeverity, fmtTimestamp } from "@/lib/api";
import { StatusPill } from "@/components/ui";

const initialState: DecideActionState = { status: "idle" };

const RATIONALE_OUTCOME_SEVERITY = {
  passed: "good",
  failed: "critical",
  not_applicable: "neutral",
} as const;

/** The Phase 5 decision panel: shows an existing decision, or -- only for a
 * case that is actually `diagnosed` -- the "Decide" action that invokes the
 * real policy engine (`POST /recovery/cases/{id}/decide`). Never executes a
 * recovery action; a decision is not an action (Phase 5 Architecture
 * Revision).
 */
export function DecidePanel({
  caseId,
  caseState,
  initialDecision,
}: {
  caseId: string;
  caseState: string;
  initialDecision: Decision | null;
}) {
  const decideWithId = decideCase.bind(null, caseId);
  const [state, formAction, pending] = useActionState(decideWithId, initialState);
  const router = useRouter();

  useEffect(() => {
    if (state.status === "success") {
      // The case's own state (and lifecycle stepper / transition history
      // elsewhere on this Server Component page) also changed as a result
      // of this decision -- refresh so the whole page reflects it, not
      // just this panel.
      router.refresh();
    }
  }, [state, router]);

  const decision = state.status === "success" ? state.decision : initialDecision;

  return (
    <div className="flex flex-col gap-4 text-sm">
      {decision ? (
        <DecisionSummary decision={decision} />
      ) : caseState === "diagnosed" ? (
        <form action={formAction} className="flex flex-col gap-3">
          <p className="text-text-muted">
            No decision has been made yet. Running the policy engine evaluates this case&apos;s
            diagnosis deterministically and records the result -- it never contacts an AI model
            and never executes a recovery action.
          </p>
          <button
            type="submit"
            disabled={pending}
            aria-busy={pending}
            className="self-start border border-signal/50 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-signal hover:bg-signal/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Deciding…" : "Decide"}
          </button>
        </form>
      ) : (
        <p className="text-sm text-text-muted">
          This case has not been diagnosed yet. A decision can only be made once a diagnosis
          exists (state: {caseState.replace(/_/g, " ")}).
        </p>
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

function DecisionSummary({ decision }: { decision: Decision }) {
  const downgraded = decision.candidate_strategy !== decision.approved_strategy;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill
          label={decision.decision_status}
          severity={decisionSeverity(decision.decision_status)}
        />
        <span className="font-mono text-xs uppercase tracking-wide text-text-dim">
          {decision.recoverability.replace(/_/g, " ")}
        </span>
      </div>

      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
        <dt className="text-text-dim">Candidate strategy</dt>
        <dd className="text-text">{decision.candidate_strategy.replace(/_/g, " ")}</dd>
        <dt className="text-text-dim">Approved strategy</dt>
        <dd className={downgraded ? "text-warn" : "text-text"}>
          {decision.approved_strategy.replace(/_/g, " ")}
          {downgraded ? " (downgraded)" : ""}
        </dd>
        <dt className="text-text-dim">Scheduled not before</dt>
        <dd className="text-text">
          {decision.scheduled_not_before ? fmtTimestamp(decision.scheduled_not_before) : "—"}
        </dd>
        <dt className="text-text-dim">Decided</dt>
        <dd className="text-text">{fmtTimestamp(decision.created_at)}</dd>
      </dl>

      <div>
        <p className="mb-1.5 font-mono text-[11px] uppercase tracking-widest text-text-dim">
          Rationale
        </p>
        <ul className="flex flex-col gap-1">
          {decision.rationale.map((entry, i) => (
            <li
              key={`${entry.rule_id}-${i}`}
              className="flex flex-wrap items-center gap-2 border border-border/60 bg-bg-inset px-2.5 py-1.5 text-xs"
            >
              <StatusPill
                label={entry.outcome.replace(/_/g, " ")}
                severity={RATIONALE_OUTCOME_SEVERITY[entry.outcome]}
              />
              <span className="font-mono text-text">{entry.rule_id}</span>
              {entry.reason_code && (
                <span className="text-text-dim">— {entry.reason_code.replace(/_/g, " ")}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <p className="font-mono text-[11px] text-text-dim">
        decision engine v{decision.decision_engine_version}
      </p>
    </div>
  );
}
