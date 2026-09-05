import Link from "next/link";
import type { Metadata } from "next";
import {
  apiGet,
  caseSeverity,
  fmtTimestamp,
  OPEN_STATES,
  type RecoveryCase,
} from "@/lib/api";
import { BackendUnavailable, EmptyState, Panel, Readout, StatusPill } from "@/components/ui";

export const metadata: Metadata = { title: "Recovery cases" };

export default async function RecoveryCasesPage() {
  const cases = await apiGet<RecoveryCase[]>("/recovery/cases");
  if (!cases.ok) return <BackendUnavailable />;

  const open = cases.data.filter((c) => OPEN_STATES.has(c.state));
  const closed = cases.data.filter((c) => !OPEN_STATES.has(c.state));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Recovery cases</h1>
        <p className="mt-1 text-sm text-text-muted">
          One case per at-risk payment, moving through an explicit, append-only-audited
          lifecycle from <span className="font-mono text-text">detected</span> to a terminal
          outcome.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border border-border bg-bg-inset px-4 py-3 font-mono text-xs">
        <Readout label="OPEN" value={open.length} />
        <Readout label="CLOSED" value={closed.length} />
        <Readout label="TOTAL" value={cases.data.length} />
      </div>

      <Panel title="Cases">
        {cases.data.length === 0 ? (
          <EmptyState>No recovery cases yet &mdash; start one from the risk queue</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">Recovery cases, open cases first</caption>
              <thead>
                <tr className="border-b border-border font-mono text-[11px] uppercase tracking-wide text-text-dim">
                  <th scope="col" className="py-2 pr-4 font-medium">Case</th>
                  <th scope="col" className="py-2 pr-4 font-medium">State</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Opened</th>
                  <th scope="col" className="py-2 font-medium">Closed</th>
                </tr>
              </thead>
              <tbody>
                {[...open, ...closed].map((c) => (
                  <tr key={c.id} className="border-b border-border/60">
                    <td className="py-2 pr-4 font-mono text-xs">
                      <Link
                        href={`/recovery/${c.id}`}
                        className="text-text underline decoration-border underline-offset-4 hover:decoration-signal hover:text-signal"
                      >
                        {c.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <StatusPill
                        label={c.state.replace(/_/g, " ")}
                        severity={caseSeverity(c.state)}
                        live={c.state === "diagnosing"}
                      />
                    </td>
                    <td className="tabular py-2 pr-4 font-mono text-xs text-text-muted">
                      {fmtTimestamp(c.opened_at)}
                    </td>
                    <td className="tabular py-2 font-mono text-xs text-text-muted">
                      {c.closed_at ? fmtTimestamp(c.closed_at) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
