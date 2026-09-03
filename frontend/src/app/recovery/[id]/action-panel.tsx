"use client";

import { useActionState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { scheduleAction, executeAction, type ActionActionState } from "./action-action";
import type { Action } from "@/lib/api";
import { actionStatusSeverity, fmtTimestamp } from "@/lib/api";
import { StatusPill } from "@/components/ui";

const initialState: ActionActionState = { status: "idle" };

/** The Phase 6 action panel: shows a scheduled/executed action, or -- only
 * for a case whose decision is actually approved and pending scheduling --
 * the "Schedule action" control that invokes the real executor
 * (`POST /recovery/cases/{id}/schedule-action`), followed by "Execute
 * action" (`POST .../execute-action`) once scheduled. A `retry` /
 * `request_payment_method_update` / `contact_customer` action may need more
 * than one "Execute action" click if a simulated attempt is a temporary
 * failure and the retry cap has not been reached -- the panel keeps
 * offering the control while `action.status === "scheduled"`.
 *
 * Never accepts a strategy from this UI: the action type shown is always
 * the decision's own `approved_strategy`, read back from the persisted
 * action -- an operator cannot choose what runs here (ADR-003). Executing
 * `no_action` / `manual_review` never claims money moved; every other
 * strategy runs against a deterministic, explicitly SIMULATED provider (see
 * app/decision/providers.py) -- never a real payment gateway or messaging
 * system -- and every outcome shown here is labeled as simulated.
 */
export function ActionPanel({
  caseId,
  caseState,
  decisionStatus,
  initialAction,
}: {
  caseId: string;
  caseState: string;
  decisionStatus: string | null;
  initialAction: Action | null;
}) {
  const scheduleWithId = scheduleAction.bind(null, caseId);
  const executeWithId = executeAction.bind(null, caseId);
  const [scheduleState, scheduleFormAction, schedulePending] = useActionState(
    scheduleWithId,
    initialState,
  );
  const [executeState, executeFormAction, executePending] = useActionState(
    executeWithId,
    initialState,
  );
  const router = useRouter();

  useEffect(() => {
    if (scheduleState.status === "success" || executeState.status === "success") {
      router.refresh();
    }
  }, [scheduleState, executeState, router]);

  const action =
    executeState.status === "success"
      ? executeState.action
      : scheduleState.status === "success"
        ? scheduleState.action
        : initialAction;

  const activeError =
    executeState.status === "error" ? executeState : scheduleState.status === "error" ? scheduleState : null;

  return (
    <div className="flex flex-col gap-4 text-sm">
      {action ? (
        <ActionSummary action={action} />
      ) : decisionStatus === "approved" && caseState === "decision_pending" ? (
        <form action={scheduleFormAction} className="flex flex-col gap-3">
          <p className="text-text-muted">
            This decision is approved but no action has been scheduled yet. Scheduling records the
            action this system will carry out -- it never invents a strategy the policy engine did
            not already approve.
          </p>
          <button
            type="submit"
            disabled={schedulePending}
            aria-busy={schedulePending}
            className="self-start border border-signal/50 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-signal hover:bg-signal/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {schedulePending ? "Scheduling…" : "Schedule action"}
          </button>
        </form>
      ) : (
        <p className="text-sm text-text-muted">
          No action can be scheduled yet (case state: {caseState.replace(/_/g, " ")}
          {decisionStatus ? `, decision: ${decisionStatus}` : ""}). An action can only be
          scheduled for an approved decision.
        </p>
      )}

      {action && action.status === "scheduled" && (
        <form action={executeFormAction} className="flex flex-col gap-3">
          <button
            type="submit"
            disabled={executePending}
            aria-busy={executePending}
            className="self-start border border-signal/50 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-signal hover:bg-signal/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {executePending ? "Executing…" : "Execute action"}
          </button>
        </form>
      )}

      {activeError && (
        <div
          role="alert"
          className="border border-critical/40 bg-critical/[0.06] px-3 py-2 text-xs text-critical"
        >
          {activeError.message}
        </div>
      )}
    </div>
  );
}

function ActionSummary({ action }: { action: Action }) {
  const latest = action.executions.at(-1) ?? null;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill label={action.status} severity={actionStatusSeverity(action.status)} />
        <span className="font-mono text-xs uppercase tracking-wide text-text-dim">
          {action.action_type.replace(/_/g, " ")}
        </span>
      </div>

      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
        <dt className="text-text-dim">Scheduled</dt>
        <dd className="text-text">{fmtTimestamp(action.created_at)}</dd>
        <dt className="text-text-dim">Attempts</dt>
        <dd className="text-text">{action.executions.length}</dd>
      </dl>

      {latest && (
        <div className="border border-border/60 bg-bg-inset px-3 py-2.5">
          <p className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
            Execution outcome
          </p>
          <p className="mt-1 text-text">{latest.outcome.replace(/_/g, " ")}</p>
          {latest.outcome === "simulated_success" && (
            <p className="mt-1 text-xs text-text-dim">
              A deterministic, simulated payment/recovery provider reported this attempt
              succeeded -- no real payment gateway was contacted. The resulting simulated
              payment evidence is what Phase 7&apos;s observation reads to mark this case recovered.
            </p>
          )}
          {latest.outcome === "simulated_temporary_failure" && (
            <p className="mt-1 text-xs text-text-dim">
              The simulated provider reported a temporary failure on this attempt. If the retry
              cap has not been reached, executing again attempts the next try.
            </p>
          )}
          {latest.outcome === "simulated_permanent_failure" && (
            <p className="mt-1 text-xs text-text-dim">
              The simulated provider reported a non-retriable failure. No further attempts will
              be made for this action.
            </p>
          )}
          {latest.outcome === "deferred_no_integration" && (
            <p className="mt-1 text-xs text-text-dim">
              Recorded before the simulated execution layer existed -- no processor integration
              existed at that time. Historical, not produced by current code.
            </p>
          )}
          <p className="mt-1 font-mono text-[11px] text-text-dim">
            attempt {latest.attempt_no} · {latest.idempotency_key} · {fmtTimestamp(latest.created_at)}
          </p>
          {latest.simulated_reference && (
            <p className="mt-1 font-mono text-[11px] text-text-dim">
              simulated reference: {latest.simulated_reference}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
