export default function Loading() {
  return (
    <div
      className="flex min-h-[45vh] items-center justify-center"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-text-dim">
        <span className="h-1.5 w-1.5 rounded-full bg-signal pulse-dot" aria-hidden="true" />
        Loading
      </div>
    </div>
  );
}
