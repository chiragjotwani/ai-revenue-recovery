type HealthResponse = {
  status: string;
  environment: string;
};

async function getBackendHealth(): Promise<HealthResponse | null> {
  // Server-only var (not NEXT_PUBLIC_): this fetch always runs server-side,
  // so it must be read at container runtime, not inlined at build time.
  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${baseUrl}/health`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await getBackendHealth();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-lg flex-col items-center gap-6 px-8 py-16 text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">
          AI Revenue Recovery Platform
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400">Phase 2 &mdash; Revenue Risk Detection</p>
        <div
          className={`rounded-lg border px-6 py-4 text-sm ${
            health
              ? "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-400"
              : "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400"
          }`}
        >
          {health ? (
            <>
              Backend status: <strong>{health.status}</strong> ({health.environment})
            </>
          ) : (
            <>Backend unreachable. Is the API running?</>
          )}
        </div>
        <a
          href="/risk"
          className="text-sm font-medium text-zinc-700 underline underline-offset-4 dark:text-zinc-300"
        >
          View Revenue Risk Dashboard &rarr;
        </a>
      </main>
    </div>
  );
}
