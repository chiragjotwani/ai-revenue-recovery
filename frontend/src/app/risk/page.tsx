import Link from "next/link";
import type { Metadata } from "next";
import {
  apiGet,
  riskSeverity,
  type RecoveryCase,
  type RiskAssessment,
  type RiskSummary,
} from "@/lib/api";
import { BackendUnavailable, EmptyState, Panel, Readout, ScoreMeter, StatusPill } from "@/components/ui";
import { openRecoveryCase } from "./actions";

export const metadata: Metadata = { title: "Risk queue" };

export default async function RiskQueuePage() {
  const [summary, payments, cases] = await Promise.all([
    apiGet<RiskSummary>("/risk/summary"),
    apiGet<RiskAssessment[]>("/risk/payments"),
    apiGet<RecoveryCase[]>("/recovery/cases"),
  ]);

  if (!summary.ok || !payments.ok) return <BackendUnavailable />;

  const caseByPayment = new Map<string, RecoveryCase>();
  if (cases.ok) for (const c of cases.data) caseByPayment.set(c.payment_id, c);

  const ordered = [...payments.data].sort((a, b) => b.risk_score - a.risk_score);
  const levels = summary.data.risk_level_breakdown;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Risk queue</h1>
        <p className="mt-1 text-sm text-text-muted">
          Failed payments with no later successful payment for the same customer, ordered by
          risk score. Work the top of the list first.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border border-border bg-bg-inset px-4 py-3 font-mono text-xs">
        <Readout
          label="AT RISK"
          value={`${payments.data.length} payment${payments.data.length === 1 ? "" : "s"}`}
        />
        <Readout label="HIGH" value={levels.high} />
        <Readout label="MEDIUM" value={levels.medium} />
        <Readout label="LOW" value={levels.low} />
      </div>

      <Panel title="At-risk payments">
        {ordered.length === 0 ? (
          <EmptyState>No revenue currently at risk</EmptyState>
        ) : (
          <ul className="flex flex-col divide-y divide-border/60">
            {ordered.map((p) => {
              const existing = caseByPayment.get(p.payment_id);
              return (
                <li
                  key={p.payment_id}
                  className="flex flex-col gap-3 py-4 md:flex-row md:items-center md:justify-between"
                >
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-3">
                      <span className="tabular text-lg font-semibold text-text">
                        {p.amount} {p.currency}
                      </span>
                      <StatusPill label={p.risk_level} severity={riskSeverity(p.risk_level)} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs text-text-muted">
                      <span className="text-text-dim">{p.external_reference}</span>
                      <span>reason: {p.failure_reason ?? "unknown"}</span>
                      <span>
                        {p.consecutive_failures} consecutive failure
                        {p.consecutive_failures === 1 ? "" : "s"}
                      </span>
                      <span>
                        success rate {(p.historical_success_rate * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <ScoreMeter
                      value={p.risk_score}
                      severity={riskSeverity(p.risk_level)}
                      caption={`risk score ${p.risk_score.toFixed(2)} (${p.risk_level})`}
                    />
                    {existing ? (
                      <Link
                        href={`/recovery/${existing.id}`}
                        className="border border-border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-text hover:border-signal hover:text-signal"
                      >
                        Open case &rarr;
                      </Link>
                    ) : (
                      <form action={openRecoveryCase}>
                        <input type="hidden" name="payment_id" value={p.payment_id} />
                        <button
                          type="submit"
                          className="border border-border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-text hover:border-signal hover:text-signal"
                        >
                          Start case
                        </button>
                      </form>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <p className="text-xs text-text-dim">
        &ldquo;Start case&rdquo; opens the recovery case file only. No retry or other financial
        action is executed &mdash; that is Phase 5/6.
      </p>
    </div>
  );
}
