import Link from "next/link";
import { notFound } from "next/navigation";
import {
  apiGet,
  caseSeverity,
  fmtTimestamp,
  provenanceLabel,
  riskSeverity,
  TERMINAL_STATES,
  type RecoveryCaseDetail,
  type RiskAssessment,
} from "@/lib/api";
import { BackendUnavailable, Panel, ScoreMeter, StatusPill } from "@/components/ui";
import { DecidePanel } from "./decide-panel";
import { ActionPanel } from "./action-panel";

export async function generateMetadata({ params }: PageProps<"/recovery/[id]">) {
  const { id } = await params;
  return { title: `Case ${id.slice(0, 8)}` };
}

const LIFECYCLE = [
  "detected",
  "diagnosing",
  "diagnosed",
  "decision_pending",
  "action_scheduled",
  "action_executed",
  "observing",
  "recovered",
] as const;

export default async function RecoveryCaseDetailPage({ params }: PageProps<"/recovery/[id]">) {
  const { id } = await params;
  const [caseRes, paymentsRes] = await Promise.all([
    apiGet<RecoveryCaseDetail>(`/recovery/cases/${id}`),
    apiGet<RiskAssessment[]>("/risk/payments"),
  ]);

  if (!caseRes.ok) {
    // not_found (unknown id OR malformed reference) -> 404 page.
    // unavailable (transport / 5xx) -> backend-unavailable state. (BUG-004)
    if (caseRes.kind === "not_found") notFound();
    return <BackendUnavailable />;
  }

  const c = caseRes.data;
  const risk = paymentsRes.ok
    ? paymentsRes.data.find((p) => p.payment_id === c.payment_id) ?? null
    : null;
  const isTerminal = TERMINAL_STATES.has(c.state);
  const stepIndex = LIFECYCLE.indexOf(c.state as (typeof LIFECYCLE)[number]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href="/recovery"
          className="font-mono text-xs uppercase tracking-widest text-text-dim hover:text-signal"
        >
          &larr; All recovery cases
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-lg font-semibold tracking-tight">
            Case {c.id.slice(0, 8)}
          </h1>
          <StatusPill
            label={c.state.replace(/_/g, " ")}
            severity={caseSeverity(c.state)}
            live={c.state === "diagnosing"}
          />
        </div>
        <p className="mt-1 font-mono text-xs text-text-dim">
          opened {fmtTimestamp(c.opened_at)}
          {c.closed_at ? ` · closed ${fmtTimestamp(c.closed_at)}` : ""}
        </p>
      </div>

      {/* PAYMENT -> RISK */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="1 · Payment">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-text-dim">Amount</dt>
            <dd className="tabular font-mono text-text">
              {risk ? `${risk.amount} ${risk.currency}` : "—"}
            </dd>
            <dt className="text-text-dim">Failure reason</dt>
            <dd className="text-text">{risk?.failure_reason ?? "—"}</dd>
            <dt className="text-text-dim">Reference</dt>
            <dd className="font-mono text-xs text-text-muted">{risk?.external_reference ?? "—"}</dd>
            <dt className="text-text-dim">Payment id</dt>
            <dd className="font-mono text-xs text-text-muted">{c.payment_id}</dd>
          </dl>
        </Panel>
        <Panel title="2 · Risk">
          {risk ? (
            <div className="flex flex-col gap-3 text-sm">
              <div className="flex items-center gap-3">
                <ScoreMeter
                  value={risk.risk_score}
                  severity={riskSeverity(risk.risk_level)}
                  caption={`risk score ${risk.risk_score.toFixed(2)}`}
                />
                <StatusPill label={risk.risk_level} severity={riskSeverity(risk.risk_level)} />
              </div>
              <p className="text-text-muted">
                {risk.consecutive_failures} consecutive failure
                {risk.consecutive_failures === 1 ? "" : "s"} · historical success rate{" "}
                {(risk.historical_success_rate * 100).toFixed(0)}%
              </p>
            </div>
          ) : (
            <p className="text-sm text-text-muted">
              This payment is not in the current at-risk set (it may have since succeeded, or
              the case is closed).
            </p>
          )}
        </Panel>
      </div>

      {/* AI DIAGNOSIS -> RECOMMENDATION */}
      <Panel
        title="3 · AI diagnosis"
        action={
          c.diagnosis ? (
            <ProvenanceTag modelName={c.diagnosis.model_name} />
          ) : (
            <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">
              not run
            </span>
          )
        }
      >
        {c.diagnosis ? (
          <div className="flex flex-col gap-4 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-lg font-semibold text-text">
                {c.diagnosis.outcome.replace(/_/g, " ")}
              </span>
              <span className="font-mono text-xs uppercase tracking-wide text-text-dim">
                {c.diagnosis.disposition.replace(/_/g, " ")}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <ScoreMeter
                value={c.diagnosis.confidence}
                severity="signal"
                caption={`model-reported confidence ${c.diagnosis.confidence.toFixed(2)}`}
              />
              <span className="text-xs text-text-dim">
                model-reported confidence (not a calibrated probability)
              </span>
            </div>
            <p className="text-text-muted">{c.diagnosis.reasoning}</p>

            <div className="border border-signal/30 bg-signal/[0.06] px-3 py-2.5">
              <p className="font-mono text-[11px] uppercase tracking-widest text-signal">
                Recommendation — advisory only
              </p>
              <p className="mt-1 text-text">
                {c.diagnosis.recommended_strategy.replace(/_/g, " ")}
                {c.diagnosis.recommended_delay_hours != null
                  ? ` after ${c.diagnosis.recommended_delay_hours}h`
                  : ""}
              </p>
              <p className="mt-1 text-xs text-text-dim">
                The Phase 5 policy engine decides what actually happens. Nothing is scheduled or
                executed from this screen.
              </p>
            </div>

            <p className="font-mono text-[11px] text-text-dim">
              {c.diagnosis.model_name}/{c.diagnosis.model_version} · {c.diagnosis.prompt_version}{" "}
              · schema v{c.diagnosis.schema_version} · {c.diagnosis.latency_ms} ms ·{" "}
              {fmtTimestamp(c.diagnosis.created_at)}
            </p>
          </div>
        ) : (
          <p className="text-sm text-text-muted">
            No diagnosis has been run for this case yet. In this phase, diagnosis is triggered
            through the API (<span className="font-mono text-xs">POST /recovery/cases/{c.id.slice(0, 8)}…/diagnose</span>);
            an operator trigger is out of scope until the decision engine exists.
          </p>
        )}
      </Panel>

      {/* POLICY DECISION */}
      <Panel title="4 · Decision">
        <DecidePanel caseId={c.id} caseState={c.state} initialDecision={c.decision} />
      </Panel>

      {/* ACTION SCHEDULING -> EXECUTION */}
      <Panel title="5 · Action">
        <ActionPanel
          caseId={c.id}
          caseState={c.state}
          decisionStatus={c.decision?.decision_status ?? null}
          initialAction={c.action}
        />
      </Panel>

      {/* RECOVERY STATUS -> OUTCOME */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="6 · Recovery status">
          <ol className="flex flex-col gap-1.5 font-mono text-xs">
            {LIFECYCLE.map((state, i) => {
              const done = stepIndex >= 0 && i < stepIndex;
              const current = state === c.state;
              return (
                <li
                  key={state}
                  className={`flex items-center gap-2 ${
                    current ? "text-signal" : done ? "text-text-muted" : "text-text-dim"
                  }`}
                >
                  <span aria-hidden="true">{current ? "▶" : done ? "✓" : "·"}</span>
                  <span className="uppercase tracking-wide">{state.replace(/_/g, " ")}</span>
                </li>
              );
            })}
          </ol>
          {isTerminal && c.state !== "recovered" && (
            <p className="mt-2 font-mono text-xs uppercase tracking-wide text-critical">
              ended: {c.state}
            </p>
          )}
        </Panel>
        <Panel title="7 · Outcome">
          {c.state === "recovered" ? (
            <p className="text-sm text-good">Recovered · closed {fmtTimestamp(c.closed_at ?? "")}</p>
          ) : isTerminal ? (
            <p className="text-sm text-critical">
              {c.state === "abandoned" ? "Abandoned" : "Failed"} · closed{" "}
              {fmtTimestamp(c.closed_at ?? "")}
            </p>
          ) : (
            <p className="text-sm text-text-muted">
              In progress. No outcome yet — recovered revenue is measured in Phase 8.
            </p>
          )}
        </Panel>
      </div>

      {/* AUDIT TRAIL */}
      <Panel title="Transition history">
        <ol className="flex flex-col gap-2">
          {c.history.map((t) => (
            <li key={t.id} className="border border-border/60 bg-bg-inset px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-text">
                  {(t.from_state ?? "∅").replace(/_/g, " ")} &rarr; {t.to_state.replace(/_/g, " ")}
                </span>
                <span className="font-mono text-[11px] text-text-dim">
                  {fmtTimestamp(t.created_at)}
                </span>
              </div>
              <p className="mt-0.5 font-mono text-[11px] text-text-dim">
                by {t.actor}
                {t.reason ? ` — ${t.reason}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}

function ProvenanceTag({ modelName }: { modelName: string }) {
  const p = provenanceLabel(modelName);
  return <StatusPill label={p.text} severity={p.severity} />;
}
