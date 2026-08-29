"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";

export default function ErrorPage({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    // No telemetry stack yet (Phase 14) -- console is the only sink.
    console.error("route error boundary:", error);
  }, [error]);

  return (
    <div className="flex min-h-[45vh] items-center justify-center">
      <div className="flex max-w-md flex-col items-center gap-3 border border-critical/40 bg-critical/10 px-6 py-6 text-center">
        <span className="font-mono text-sm uppercase tracking-widest text-critical">
          Something went wrong
        </span>
        <p className="text-sm text-text-muted">
          An unexpected error occurred while rendering this view. It has been logged to the
          browser console.
        </p>
        <button
          type="button"
          onClick={retry}
          className="mt-1 border border-border px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-text hover:border-signal hover:text-signal"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
