import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Nav } from "@/components/nav";
import { apiGet, formatCurrencyMap, type RiskSummary } from "@/lib/api";

const sans = Geist({ variable: "--font-app-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-app-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: {
    default: "AI Revenue Recovery",
    template: "%s · AI Revenue Recovery",
  },
  description:
    "Operator console for detecting revenue at risk from failed payments, diagnosing the cause with a bounded AI layer, and tracking recovery to outcome.",
  applicationName: "AI Revenue Recovery",
  robots: { index: false, follow: false },
};

async function globalTicker(): Promise<RiskSummary | null> {
  const res = await apiGet<RiskSummary>("/risk/summary");
  return res.ok ? res.data : null;
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const ticker = await globalTicker();

  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} h-full`}>
      <body className="flex min-h-full flex-col bg-bg text-text antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:border focus:border-signal focus:bg-bg focus:px-3 focus:py-1.5 focus:font-mono focus:text-xs focus:text-signal"
        >
          Skip to content
        </a>

        <header className="sticky top-0 z-10 border-b border-border bg-bg/95 backdrop-blur-sm">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
            <Link
              href="/"
              className="font-mono text-sm font-semibold tracking-widest text-text"
              aria-label="AI Revenue Recovery, overview"
            >
              ARR<span className="text-signal">/</span>CONSOLE
            </Link>
            <Nav />
          </div>
          <div className="border-t border-border bg-bg-inset">
            <div className="mx-auto flex max-w-6xl items-center gap-6 overflow-x-auto px-6 py-1.5 font-mono text-[11px] tracking-wide text-text-dim">
              {ticker ? (
                <>
                  <span className="flex items-center gap-1.5 text-signal">
                    <span
                      className="h-1.5 w-1.5 rounded-full bg-signal pulse-dot"
                      aria-hidden="true"
                    />
                    LIVE
                  </span>
                  <span className="whitespace-nowrap">
                    REVENUE AT RISK{" "}
                    <span className="tabular text-text">
                      {formatCurrencyMap(ticker.currency_breakdown)}
                    </span>
                  </span>
                  <span className="whitespace-nowrap">
                    OPEN SIGNALS{" "}
                    <span className="tabular text-text">{ticker.at_risk_payment_count}</span>
                  </span>
                </>
              ) : (
                <span className="text-critical">BACKEND UNAVAILABLE</span>
              )}
            </div>
          </div>
        </header>

        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
          {children}
        </main>

        <footer className="border-t border-border px-6 py-4 text-center font-mono text-[11px] text-text-dim">
          The AI diagnoses only. It never moves money or executes an action &mdash; every
          recommendation is validated by policy and state checks first (ADR-003).
        </footer>
      </body>
    </html>
  );
}
