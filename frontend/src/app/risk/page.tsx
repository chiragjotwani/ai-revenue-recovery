import Link from "next/link";

type RiskAssessment = {
  payment_id: string;
  customer_id: string;
  external_reference: string;
  amount: string;
  currency: string;
  failure_reason: string | null;
  consecutive_failures: number;
  historical_success_rate: number;
  risk_score: number;
  risk_level: "low" | "medium" | "high";
};

type RiskSummary = {
  at_risk_payment_count: number;
  revenue_at_risk: string;
  currency_breakdown: Record<string, string>;
  risk_level_breakdown: { low: number; medium: number; high: number };
};

async function getRiskData(): Promise<{
  summary: RiskSummary | null;
  payments: RiskAssessment[] | null;
}> {
  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  try {
    const [summaryRes, paymentsRes] = await Promise.all([
      fetch(`${baseUrl}/risk/summary`, { cache: "no-store" }),
      fetch(`${baseUrl}/risk/payments`, { cache: "no-store" }),
    ]);
    if (!summaryRes.ok || !paymentsRes.ok) return { summary: null, payments: null };
    return {
      summary: (await summaryRes.json()) as RiskSummary,
      payments: (await paymentsRes.json()) as RiskAssessment[],
    };
  } catch {
    return { summary: null, payments: null };
  }
}

const RISK_LEVEL_STYLES: Record<RiskAssessment["risk_level"], string> = {
  low: "bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/30",
  medium: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30",
  high: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30",
};

function formatCurrencyBreakdown(breakdown: Record<string, string>): string {
  const entries = Object.entries(breakdown);
  if (entries.length === 0) return "0";
  return entries.map(([currency, amount]) => `${amount} ${currency}`).join(" + ");
}

export default async function RiskDashboard() {
  const { summary, payments } = await getRiskData();

  if (!summary || !payments) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-black">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-700 dark:text-red-400">
          Backend unreachable. Is the API running?
        </div>
      </div>
    );
  }

  const { risk_level_breakdown: levels } = summary;

  return (
    <div className="min-h-screen bg-zinc-50 px-8 py-12 dark:bg-black">
      <main className="mx-auto flex max-w-4xl flex-col gap-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-black dark:text-zinc-50">
            Revenue Risk Dashboard
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Failed payments with no subsequent successful payment for the same customer.{" "}
            <Link
              href="/recovery"
              className="underline underline-offset-4 hover:text-zinc-900 dark:hover:text-zinc-200"
            >
              Recovery cases
            </Link>
          </p>
        </div>

        <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile
            label="Revenue at risk"
            value={formatCurrencyBreakdown(summary.currency_breakdown)}
          />
          <StatTile label="At-risk payments" value={String(summary.at_risk_payment_count)} />
          <StatTile
            label="Risk level breakdown"
            value={`${levels.low} low / ${levels.medium} medium / ${levels.high} high`}
          />
        </section>

        <section>
          <h2 className="mb-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
            At-risk payments
          </h2>
          {payments.length === 0 ? (
            <p className="text-sm text-zinc-500">No revenue currently at risk.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-2 font-medium">Reference</th>
                    <th className="px-4 py-2 font-medium">Amount</th>
                    <th className="px-4 py-2 font-medium">Failure reason</th>
                    <th className="px-4 py-2 font-medium">Consecutive failures</th>
                    <th className="px-4 py-2 font-medium">Score</th>
                    <th className="px-4 py-2 font-medium">Level</th>
                  </tr>
                </thead>
                <tbody>
                  {payments.map((p) => (
                    <tr
                      key={p.payment_id}
                      className="border-t border-zinc-200 dark:border-zinc-800"
                    >
                      <td className="px-4 py-2 font-mono text-xs">{p.external_reference}</td>
                      <td className="px-4 py-2">
                        {p.amount} {p.currency}
                      </td>
                      <td className="px-4 py-2">{p.failure_reason ?? "—"}</td>
                      <td className="px-4 py-2">{p.consecutive_failures}</td>
                      <td className="px-4 py-2">{p.risk_score.toFixed(2)}</td>
                      <td className="px-4 py-2">
                        <span
                          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${RISK_LEVEL_STYLES[p.risk_level]}`}
                        >
                          {p.risk_level}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-5 py-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className="mt-1 text-lg font-semibold text-black dark:text-zinc-50">{value}</div>
    </div>
  );
}
