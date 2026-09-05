import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[45vh] items-center justify-center">
      <div className="flex max-w-md flex-col items-center gap-3 border border-border bg-bg-raised px-6 py-6 text-center">
        <span className="font-mono text-sm uppercase tracking-widest text-text-dim">
          404 &mdash; not found
        </span>
        <p className="text-sm text-text-muted">
          No page or record matches this address. If you followed a link to a recovery case,
          that case reference is not valid.
        </p>
        <div className="mt-1 flex gap-2">
          <Link
            href="/"
            className="border border-border px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-text hover:border-signal hover:text-signal"
          >
            Overview
          </Link>
          <Link
            href="/recovery"
            className="border border-border px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-text hover:border-signal hover:text-signal"
          >
            Recovery cases
          </Link>
        </div>
      </div>
    </div>
  );
}
