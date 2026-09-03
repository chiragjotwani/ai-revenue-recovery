import Link from "next/link";
import {
  apiGet,
  fmtTimestamp,
  formatCurrencyAmounts,
  formatCurrencyMap,
  OPEN_STATES,
  riskSeverity,
  type BaselineComparisonReport,
  type Health,
  type ModelReport,
  type RecoveryCase,
  type RevenueReport,
  type RiskAssessment,
  type RiskSummary,
  type StrategyAnalyticsReport,
} from "@/lib/api";
import {
  BackendUnavailable,
  EmptyState,
  NotAvailableYet,
  Panel,
  Readout,
  ScoreMeter,
  StatusPill,
} from "@/components/ui";

export default async function OverviewPage() {
  const [health, summary, payments, cases, revenue, strategy, modelReport, baseline] =
    await Promise.all([
      apiGet<Health>("/health"),
      apiGet<RiskSummary>("/risk/summary"),
      apiGet<RiskAssessment[]>("/risk/payments"),
      apiGet<RecoveryCase[]>("/recovery/cases"),
      apiGet<RevenueReport>("/measurement/report"),
      apiGet<StrategyAnalyticsReport>("/analytics/strategy-report"),
      apiGet<ModelReport>("/ai/model-report"),
      apiGet<BaselineComparisonReport>("/measurement/baseline-comparison"),
    ]);

  if (!summary.ok || !payments.ok || !cases.ok) {
    return <BackendUnavailable />;
  }

  const openCases = cases.data.filter((c) => OPEN_STATES.has(c.state));
  const diagnosedOrLater = cases.data.filter((c) =>
    ["diagnosed", "decision_pending", "action_scheduled", "action_executed", "observing"].includes(
      c.state,
    ),
  );
  const topPayments = [...payments.data]
    .sort((a, b) => b.risk_score - a.risk_score)
    .slice(0, 5);
  const levels = summary.data.risk_level_breakdown;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Revenue recovery overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          What revenue is at risk, why, and where each recovery case stands.
        </p>
      </div>

      {/* 1-2. Revenue at risk + recoverable opportunity */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Panel title="Revenue at risk">
          <p className="tabular text-2xl font-semibold text-text">
            {formatCurrencyMap(summary.data.currency_breakdown)}
          </p>
          <p className="mt-1 text-xs text-text-dim">
            across {summary.data.at_risk_payment_count} failed payment
            {summary.data.at_risk_payment_count === 1 ? "" : "s"} with no later success
          </p>
        </Panel>
        <Panel title="Recoverable opportunity">
          <NotAvailableYet
            what="Recoverable share"
            why="Needs the Phase 5 decision engine to classify which at-risk payments are actually recoverable."
          />
        </Panel>
        <Panel title="Recovery performance">
          {revenue.ok ? (
            <div className="flex flex-col gap-1">
              <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
                Observed recovered
              </span>
              <p className="tabular text-2xl font-semibold text-good">
                {formatCurrencyAmounts(revenue.data.observed_recovered)}
              </p>
              <p className="text-xs text-text-dim">
                {revenue.data.recovered_case_count} of {revenue.data.eligible_case_count} eligible
                case{revenue.data.eligible_case_count === 1 ? "" : "s"} (
                {(revenue.data.observed_recovery_rate * 100).toFixed(0)}%) -- observed fact, not an
                estimate of impact.{" "}
                <Link href="/recovery" className="underline hover:text-signal">
                  Details &rarr;
                </Link>
              </p>
            </div>
          ) : (
            <NotAvailableYet
              what="Recovered revenue"
              why="Needs Phase 8 revenue measurement (outcomes + control group)."
            />
          )}
        </Panel>
      </div>

      {/* 3. Active recovery cases */}
      <Panel
        title="Active recovery cases"
        action={
          <Link
            href="/recovery"
            className="font-mono text-[11px] uppercase tracking-widest text-text-dim hover:text-signal"
          >
            All cases &rarr;
          </Link>
        }
      >
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2 font-mono text-xs">
          <Readout label="OPEN" value={openCases.length} />
          <Readout label="DIAGNOSED+" value={diagnosedOrLater.length} signal />
          <Readout
            label="CLOSED"
            value={cases.data.length - openCases.length}
          />
          <Readout label="RISK MIX" value={`${levels.high} high / ${levels.medium} med / ${levels.low} low`} />
          {revenue.ok && (
            <Readout
              label="STOPPED / UNRECOVERED"
              value={
                revenue.data.observed_not_recovered.reduce((n, r) => n + r.case_count, 0) +
                revenue.data.unresolved.reduce((n, r) => n + r.case_count, 0)
              }
            />
          )}
        </div>
      </Panel>

      {/* 5. Highest-priority cases */}
      <Panel title="Highest-priority at-risk payments">
        {topPayments.length === 0 ? (
          <EmptyState>No revenue currently at risk</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">
                At-risk payments ordered by risk score, highest first
              </caption>
              <thead>
                <tr className="border-b border-border font-mono text-[11px] uppercase tracking-wide text-text-dim">
                  <th scope="col" className="py-2 pr-4 font-medium">Reference</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Amount</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Failure reason</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Risk</th>
                  <th scope="col" className="py-2 font-medium">Level</th>
                </tr>
              </thead>
              <tbody>
                {topPayments.map((p) => (
                  <tr key={p.payment_id} className="border-b border-border/60">
                    <td className="py-2 pr-4 font-mono text-xs text-text">{p.external_reference}</td>
                    <td className="tabular py-2 pr-4 font-mono text-xs">
                      {p.amount} {p.currency}
                    </td>
                    <td className="py-2 pr-4 text-text-muted">{p.failure_reason ?? "—"}</td>
                    <td className="py-2 pr-4">
                      <ScoreMeter
                        value={p.risk_score}
                        severity={riskSeverity(p.risk_level)}
                        caption={`risk score ${p.risk_score.toFixed(2)}`}
                      />
                    </td>
                    <td className="py-2">
                      <StatusPill label={p.risk_level} severity={riskSeverity(p.risk_level)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* Strategy analytics (Phase 9: observed frequency only -- no ML model) */}
      <Panel title="Strategy analytics">
        {strategy.ok ? (
          <div className="flex flex-col gap-3">
            {strategy.data.by_strategy.length === 0 ? (
              <EmptyState>No strategy history yet</EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-sm">
                  <caption className="sr-only">Empirical recovery rate by strategy</caption>
                  <thead>
                    <tr className="border-b border-border font-mono text-[11px] uppercase tracking-wide text-text-dim">
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Strategy
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Recovered / observed
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Empirical rate
                      </th>
                      <th scope="col" className="py-2 font-medium">
                        Sample
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategy.data.by_strategy.map((s) => (
                      <tr key={s.key} className="border-b border-border/60">
                        <td className="py-2 pr-4 font-mono text-xs text-text">
                          {s.key.replace(/_/g, " ")}
                        </td>
                        <td className="tabular py-2 pr-4 font-mono text-xs">
                          {s.recovered_count} / {s.observed_count}
                        </td>
                        <td className="tabular py-2 pr-4 font-mono text-xs">
                          {s.empirical_recovery_rate == null
                            ? "—"
                            : `${(s.empirical_recovery_rate * 100).toFixed(0)}%`}
                        </td>
                        <td className="py-2">
                          {s.low_sample ? (
                            <StatusPill label="low sample" severity="warn" />
                          ) : (
                            <span className="text-xs text-text-dim">n={s.observed_count}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs text-text-dim">{strategy.data.ml_model_limitation}</p>
          </div>
        ) : (
          <NotAvailableYet
            what="Strategy analytics"
            why="Needs Phase 9 strategy analytics (historical dataset + recovery-rate aggregation)."
          />
        )}
      </Panel>

      {/* Baseline vs AI-gated recovery (buildathon finalization) */}
      <Panel title="Baseline vs. AI-gated recovery">
        {baseline.ok ? (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
                  Baseline (blind retry, simulated)
                </span>
                <p className="tabular text-xl font-semibold text-text">
                  {formatCurrencyAmounts(baseline.data.baseline_simulated_recovered)}
                </p>
                <p className="text-xs text-text-dim">
                  {(baseline.data.baseline_simulated_recovery_rate * 100).toFixed(0)}% simulated
                  recovery rate
                </p>
              </div>
              <div>
                <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
                  AI-gated (observed)
                </span>
                <p className="tabular text-xl font-semibold text-good">
                  {formatCurrencyAmounts(baseline.data.ai_gated_observed_recovered)}
                </p>
                <p className="text-xs text-text-dim">
                  {(baseline.data.ai_gated_recovery_rate * 100).toFixed(0)}% observed recovery rate
                </p>
              </div>
            </div>
            <p className="text-xs text-text-dim">
              <span className="font-mono text-text">
                {baseline.data.cases_where_ai_gate_avoided_a_blind_retry}
              </span>{" "}
              case
              {baseline.data.cases_where_ai_gate_avoided_a_blind_retry === 1 ? "" : "s"} escalated
              to manual review by policy (fraud / sparse evidence / conflicting signals) -- a
              blind-retry baseline has no such check and would have retried them anyway.
            </p>
            <p className="text-xs text-text-dim">{baseline.data.methodology}</p>
          </div>
        ) : (
          <NotAvailableYet
            what="Baseline comparison"
            why="Needs at least one eligible (decided) case."
          />
        )}
      </Panel>

      {/* 6. AI insight + system */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="AI diagnosis">
          <p className="text-sm text-text-muted">
            {diagnosedOrLater.length === 0
              ? "No case has been diagnosed yet."
              : `${diagnosedOrLater.length} case${diagnosedOrLater.length === 1 ? " has" : "s have"} an AI diagnosis. Open a case to see the cause, the model-reported confidence, and its provenance.`}
          </p>
          <p className="mt-2 text-xs text-text-dim">
            The diagnosis is advisory. A deterministic policy engine (Phase 5) decides what
            happens.
          </p>
          {modelReport.ok && (
            <div className="mt-3 flex flex-col gap-1.5 border-t border-border/60 pt-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
                  Model router
                </span>
                <StatusPill
                  label={modelReport.data.router.resolved_provider}
                  severity={modelReport.data.router.substituted ? "warn" : "neutral"}
                />
                {modelReport.data.router.substituted && (
                  <span className="text-xs text-text-dim">
                    requested {modelReport.data.router.requested_provider} --{" "}
                    {modelReport.data.router.substitution_reason}
                  </span>
                )}
              </div>
              {modelReport.data.by_model.length > 0 && (
                <p className="text-xs text-text-dim">
                  {modelReport.data.by_model
                    .map(
                      (m) =>
                        `${m.model_name}: ${m.diagnosis_count} diagnos${m.diagnosis_count === 1 ? "is" : "es"}, ${m.mean_latency_ms}ms avg${m.escalation_count > 0 ? `, ${m.escalation_count} escalated` : ""}`,
                    )
                    .join(" · ")}
                </p>
              )}
            </div>
          )}
        </Panel>
        <Panel title="System">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {health.ok ? (
              <>
                <StatusPill
                  label={health.data.status}
                  severity={health.data.status === "ok" ? "good" : "critical"}
                />
                <span className="text-text-muted">
                  environment{" "}
                  <span className="font-mono text-text">{health.data.environment}</span>
                </span>
              </>
            ) : (
              <StatusPill label="unreachable" severity="critical" />
            )}
          </div>
          <p className="mt-2 text-xs text-text-dim">
            Last checked {fmtTimestamp(new Date().toISOString())}.
          </p>
        </Panel>
      </div>
    </div>
  );
}
