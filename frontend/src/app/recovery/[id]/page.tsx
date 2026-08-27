import Link from "next/link";
import { notFound } from "next/navigation";

type Transition = {
  id: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
  actor: string;
  created_at: string;
};

type RecoveryCaseDetail = {
  id: string;
  payment_id: string;
  customer_id: string;
  state: string;
  opened_at: string;
  closed_at: string | null;
  history: Transition[];
};

async function getCase(id: string): Promise<RecoveryCaseDetail | null | "unreachable"> {
  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${baseUrl}/recovery/cases/${id}`, { cache: "no-store" });
    if (res.status === 404) return null;
    if (!res.ok) return "unreachable";
    return (await res.json()) as RecoveryCaseDetail;
  } catch {
    return "unreachable";
  }
}

function fmt(ts: string): string {
  return new Date(ts).toISOString().replace("T", " ").slice(0, 19);
}

export default async function RecoveryCaseDetailPage({
  params,
}: PageProps<"/recovery/[id]">) {
  const { id } = await params;
  const data = await getCase(id);

  if (data === "unreachable") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-black">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-700 dark:text-red-400">
          Backend unreachable. Is the API running?
        </div>
      </div>
    );
  }
  if (data === null) notFound();

  return (
    <div className="min-h-screen bg-zinc-50 px-8 py-12 dark:bg-black">
      <main className="mx-auto flex max-w-3xl flex-col gap-8">
        <div>
          <Link
            href="/recovery"
            className="text-sm text-zinc-500 underline underline-offset-4 hover:text-zinc-800 dark:hover:text-zinc-300"
          >
            &larr; All recovery cases
          </Link>
          <h1 className="mt-2 font-mono text-xl font-semibold tracking-tight text-black dark:text-zinc-50">
            Case {data.id}
          </h1>
        </div>

        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Current state</dt>
            <dd className="mt-0.5 text-black dark:text-zinc-100">{data.state.replace(/_/g, " ")}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Payment</dt>
            <dd className="mt-0.5 font-mono text-xs text-black dark:text-zinc-100">
              {data.payment_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Opened</dt>
            <dd className="mt-0.5 text-black dark:text-zinc-100">{fmt(data.opened_at)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Closed</dt>
            <dd className="mt-0.5 text-black dark:text-zinc-100">
              {data.closed_at ? fmt(data.closed_at) : "—"}
            </dd>
          </div>
        </dl>

        <section>
          <h2 className="mb-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
            Transition history
          </h2>
          <ol className="flex flex-col gap-2">
            {data.history.map((t) => (
              <li
                key={t.id}
                className="rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-950"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-black dark:text-zinc-100">
                    {(t.from_state ?? "∅").replace(/_/g, " ")} &rarr; {t.to_state.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">{fmt(t.created_at)}</span>
                </div>
                <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                  by {t.actor}
                  {t.reason ? ` — ${t.reason}` : ""}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </main>
    </div>
  );
}
