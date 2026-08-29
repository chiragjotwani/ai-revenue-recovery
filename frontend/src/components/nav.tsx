"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/risk", label: "Risk queue" },
  { href: "/recovery", label: "Recovery" },
] as const;

/**
 * The one client-side island: primary nav with an active-route indicator.
 * Everything else in the app is a Server Component.
 */
export function Nav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="flex gap-1">
      {TABS.map((tab) => {
        const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={`border px-3 py-1.5 font-mono text-xs uppercase tracking-widest transition-colors ${
              active
                ? "border-signal text-signal"
                : "border-transparent text-text-muted hover:border-border hover:text-text"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
