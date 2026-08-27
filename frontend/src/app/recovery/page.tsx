import Link from "next/link";

type RecoveryCase = {
  id: string;
  payment_id: string;
  customer_id: string;
  state: string;
  opened_at: string;
  closed_at: string | null;
};

async function getCases(): Promise<RecoveryCase[] | null> {
  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${baseUrl}/recovery/cases`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as RecoveryCase[];
  } catch {
    return null;
  }
}

const OPEN_STATES = new Set([
  "detected",
  "diagnosing",
  "diagnosed",
  "decision_pending",
  "action_scheduled",
  "action_executed",
  "observing",
]);

function StateBadge({ state }: { state: string }) {
  const terminalStyle =
    state === "recovered"
      ? "bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/30"
      : "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-500/30";
  const style = OPEN_STATES.has(state)
    ? "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30"
    : terminalStyle;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${style}`}
    >
      {state.replace(/_/g, " ")}
    </span>
  );
}

export default async function RecoveryCasesPage() {
  const cases = await getCases();

  if (!cases) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-black">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-700 dark:text-red-400">
          Backend unreachable. Is the API running?
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-50 px-8 py-12 dark:bg-black">
      <main className="mx-auto flex max-w-4xl flex-col gap-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-black dark:text-zinc-50">
            Recovery Cases
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            One case per at-risk payment, moving through an explicit lifecycle from{" "}
            <span className="font-mono">detected</span> to a terminal outcome.{" "}
            <Link
              href="/risk"
              className="underline underline-offset-4 hover:text-zinc-900 dark:hover:text-zinc-200"
            >
              Revenue risk dashboard
            </Link>
          </p>
        </div>

        {cases.length === 0 ? (
          <p className="text-sm text-zinc-500">No recovery cases yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-2 font-medium">Case</th>
                  <th className="px-4 py-2 font-medium">State</th>
                  <th className="px-4 py-2 font-medium">Opened</th>
                  <th className="px-4 py-2 font-medium">Closed</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="px-4 py-2 font-mono text-xs">
                      <Link
                        href={`/recovery/${c.id}`}
                        className="underline underline-offset-4 hover:text-zinc-900 dark:hover:text-zinc-200"
                      >
                        {c.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <StateBadge state={c.state} />
                    </td>
                    <td className="px-4 py-2 text-zinc-600 dark:text-zinc-400">
                      {new Date(c.opened_at).toISOString().replace("T", " ").slice(0, 19)}
                    </td>
                    <td className="px-4 py-2 text-zinc-600 dark:text-zinc-400">
                      {c.closed_at
                        ? new Date(c.closed_at).toISOString().replace("T", " ").slice(0, 19)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
