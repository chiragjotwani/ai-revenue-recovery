import type { ReactNode } from "react";

export type Severity = "good" | "warn" | "critical" | "signal" | "neutral";

const SEVERITY_CLASS: Record<Severity, string> = {
  good: "border-good/40 bg-good/10 text-good",
  warn: "border-warn/40 bg-warn/10 text-warn",
  critical: "border-critical/40 bg-critical/10 text-critical",
  signal: "border-signal/40 bg-signal/10 text-signal",
  neutral: "border-border-strong bg-white/[0.03] text-text-muted",
};

/**
 * Status indicator: square tag, dot + always-visible text label. Colour
 * alone never carries meaning -- removing colour must leave the label
 * legible. `live` swaps the static dot for a pulsing one (motion, not
 * colour, signals liveness; respects prefers-reduced-motion).
 */
export function StatusPill({
  label,
  severity = "neutral",
  live = false,
}: {
  label: string;
  severity?: Severity;
  live?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${SEVERITY_CLASS[severity]}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full bg-current ${live ? "pulse-dot" : ""}`}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

/**
 * A 0..1 score as a calibrated bar plus the exact number. Used for both
 * risk score (Phase 2) and diagnosis confidence (Phase 4) so a score reads
 * the same everywhere. `caption` is announced for screen readers.
 */
export function ScoreMeter({
  value,
  severity = "signal",
  caption,
}: {
  value: number;
  severity?: Severity;
  caption: string;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const bar =
    severity === "good"
      ? "bg-good"
      : severity === "warn"
        ? "bg-warn"
        : severity === "critical"
          ? "bg-critical"
          : "bg-signal";
  return (
    <div
      className="flex items-center gap-2"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={Number(value.toFixed(2))}
      aria-label={caption}
    >
      <div className="relative h-1.5 w-16 shrink-0 bg-border">
        <div className={`absolute inset-y-0 left-0 ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular font-mono text-xs text-text">{value.toFixed(2)}</span>
    </div>
  );
}

/** A console "window": hairline border, small-caps mono title bar, flat. */
export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`border border-border bg-bg-raised ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-widest text-text-dim">
          {title}
        </h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Inline `LABEL value` pair for summary strips. */
export function Readout({
  label,
  value,
  signal = false,
}: {
  label: string;
  value: ReactNode;
  signal?: boolean;
}) {
  return (
    <span className="whitespace-nowrap text-text-dim">
      {label}{" "}
      <span className={`tabular font-medium ${signal ? "text-signal" : "text-text"}`}>{value}</span>
    </span>
  );
}

/** Deliberate empty state -- not a blank panel. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="py-6 text-center font-mono text-xs uppercase tracking-widest text-text-dim">
      {children}
    </p>
  );
}

/**
 * A metric that genuinely cannot be computed from real data yet. Shown, not
 * hidden, and never a fabricated number (D3).
 */
export function NotAvailableYet({ what, why }: { what: string; why: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[11px] uppercase tracking-widest text-text-dim">{what}</span>
      <span className="text-sm text-text-muted">Not available yet</span>
      <span className="text-xs text-text-dim">{why}</span>
    </div>
  );
}

/** Full-panel notice when the API cannot be reached. */
export function BackendUnavailable() {
  return (
    <div className="flex min-h-[45vh] items-center justify-center">
      <div className="max-w-md border border-critical/40 bg-critical/10 px-6 py-5 text-center">
        <p className="font-mono text-sm uppercase tracking-widest text-critical">
          Backend unavailable
        </p>
        <p className="mt-2 text-sm text-text-muted">
          The API did not respond. Data below cannot be shown until it is reachable.
        </p>
      </div>
    </div>
  );
}
