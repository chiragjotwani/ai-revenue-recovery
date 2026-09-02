import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OverviewPage from "./page";
import RecoveryCaseDetailPage from "./recovery/[id]/page";

type Route = string;

/** Route-aware fetch stub: map URL suffix -> [status, body]. */
function stubRoutes(routes: Record<Route, [number, unknown]>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const match = Object.keys(routes).find((r) => url.includes(r));
      const [status, body] = match ? routes[match] : [404, {}];
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      };
    }) as unknown as typeof fetch,
  );
}

const SUMMARY = {
  at_risk_payment_count: 1,
  revenue_at_risk: "4999.00",
  currency_breakdown: { INR: "4999.00" },
  risk_level_breakdown: { low: 1, medium: 0, high: 0 },
};
const PAYMENTS = [
  {
    payment_id: "pay-1",
    customer_id: "c-1",
    external_reference: "seed-pay-failure-0",
    amount: "4999.00",
    currency: "INR",
    failure_reason: "insufficient_funds",
    consecutive_failures: 1,
    historical_success_rate: 0.75,
    risk_score: 0.3033,
    risk_level: "low" as const,
  },
];
const CASES = [
  {
    id: "44204178-7b79-458d-be45-336ce5aa5aee",
    payment_id: "pay-1",
    customer_id: "c-1",
    state: "diagnosed",
    opened_at: "2026-08-28T14:01:49Z",
    closed_at: null,
  },
];

afterEach(() => vi.unstubAllGlobals());

describe("OverviewPage (dashboard)", () => {
  it("renders real data and labels unavailable metrics honestly", async () => {
    stubRoutes({
      "/health": [200, { status: "ok", environment: "development" }],
      "/risk/summary": [200, SUMMARY],
      "/risk/payments": [200, PAYMENTS],
      "/recovery/cases": [200, CASES],
    });
    render(await OverviewPage());

    expect(screen.getAllByText("4999.00 INR").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("seed-pay-failure-0")).toBeInTheDocument();
    // no fabricated metrics
    expect(screen.getAllByText("Not available yet").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the backend-unavailable state when the API is down", async () => {
    stubRoutes({
      "/health": [500, {}],
      "/risk/summary": [500, {}],
      "/risk/payments": [500, {}],
      "/recovery/cases": [500, {}],
    });
    render(await OverviewPage());
    expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument();
  });

  it("shows observed recovered revenue as an observed fact, never a causal claim", async () => {
    stubRoutes({
      "/health": [200, { status: "ok", environment: "development" }],
      "/risk/summary": [200, SUMMARY],
      "/risk/payments": [200, PAYMENTS],
      "/recovery/cases": [200, CASES],
      "/measurement/report": [
        200,
        {
          measurement_basis: "observed_evidence",
          counterfactual_available: false,
          counterfactual_limitation: "No randomized control group exists.",
          eligible_case_count: 4,
          eligible_at_risk: [{ currency: "INR", amount: "9998.00", case_count: 4 }],
          observed_recovered: [{ currency: "INR", amount: "4999.00", case_count: 1 }],
          observed_not_recovered: [],
          unresolved: [{ currency: "INR", amount: "4999.00", case_count: 3 }],
          recovered_case_count: 1,
          observed_recovery_rate: 0.25,
          recovered_by_strategy: [],
          recovered_by_disposition: [],
        },
      ],
    });
    render(await OverviewPage());

    expect(screen.getByText(/observed recovered/i)).toBeInTheDocument();
    expect(screen.getAllByText("4999.00 INR").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/1 of 4 eligible case/i)).toBeInTheDocument();
    expect(screen.getByText(/observed fact, not an/i)).toBeInTheDocument();
    // Never claims causality/AI-generated revenue anywhere on the page.
    expect(screen.queryByText(/ai generated/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/incremental/i)).not.toBeInTheDocument();
  });

  it("shows strategy analytics as an observed frequency, never a prediction (Phase 9)", async () => {
    stubRoutes({
      "/health": [200, { status: "ok", environment: "development" }],
      "/risk/summary": [200, SUMMARY],
      "/risk/payments": [200, PAYMENTS],
      "/recovery/cases": [200, CASES],
      "/analytics/strategy-report": [
        200,
        {
          dataset_size: 2,
          low_sample_threshold: 5,
          by_strategy: [
            {
              key: "retry",
              total_case_count: 2,
              observed_count: 2,
              recovered_count: 1,
              not_recovered_count: 1,
              unresolved_count: 0,
              empirical_recovery_rate: 0.5,
              low_sample: true,
            },
          ],
          by_disposition: [],
          ml_model_status: "not_implemented",
          ml_model_limitation: "No ML recovery-probability model or strategy optimizer is implemented.",
        },
      ],
    });
    render(await OverviewPage());

    expect(screen.getByText(/strategy analytics/i)).toBeInTheDocument();
    expect(screen.getByText("retry")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText(/low sample/i)).toBeInTheDocument();
    expect(screen.getByText(/no ml recovery-probability model/i)).toBeInTheDocument();
    expect(screen.queryByText(/optimi[sz]ed strategy/i)).not.toBeInTheDocument();
  });
});

describe("RecoveryCaseDetailPage (BUG-004: invalid id vs backend down)", () => {
  const detailProps = (id: string) => ({
    params: Promise.resolve({ id }),
    searchParams: Promise.resolve({}),
  });
  const params = detailProps("not-a-real-uuid");

  it("a malformed / unknown case id triggers notFound(), NOT the unavailable state", async () => {
    stubRoutes({
      "/recovery/cases/": [422, {}], // FastAPI path-validation error
      "/risk/payments": [200, []],
    });
    await expect(RecoveryCaseDetailPage(params)).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("a real transport failure renders the backend-unavailable state", async () => {
    stubRoutes({
      "/recovery/cases/": [503, {}],
      "/risk/payments": [200, []],
    });
    render(await RecoveryCaseDetailPage(params));
    expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument();
  });

  it("renders the diagnosis with its provenance for a real case", async () => {
    stubRoutes({
      "/recovery/cases/": [
        200,
        {
          ...CASES[0],
          history: [
            {
              id: "t1",
              from_state: null,
              to_state: "detected",
              reason: "case opened",
              actor: "api",
              created_at: "2026-08-28T14:01:49Z",
            },
          ],
          diagnosis: {
            outcome: "insufficient_funds",
            disposition: "retriable_transient",
            confidence: 0.9,
            reasoning: "Reported reason points clearly to this cause.",
            recommended_strategy: "retry",
            recommended_delay_hours: 6,
            model_name: "mock",
            model_version: "1",
            prompt_version: "diagnosis_prompt_v2",
            schema_version: "1",
            latency_ms: 0,
            created_at: "2026-08-28T14:01:49Z",
          },
        },
      ],
      "/risk/payments": [200, PAYMENTS],
    });
    render(await RecoveryCaseDetailPage(detailProps(CASES[0].id)));

    expect(screen.getByText("insufficient funds")).toBeInTheDocument();
    expect(screen.getByText(/model-reported confidence/i)).toBeInTheDocument();
    expect(screen.getByText(/mock diagnosis/i)).toBeInTheDocument(); // provenance
    expect(screen.getByText(/advisory only/i)).toBeInTheDocument();
  });

  it("renders a real decision (Phase 5F) on the case detail page", async () => {
    stubRoutes({
      "/recovery/cases/": [
        200,
        {
          ...CASES[0],
          state: "decision_pending",
          history: [],
          diagnosis: null,
          decision: {
            id: "dec-1",
            case_id: CASES[0].id,
            diagnosis_id: "diag-1",
            recoverability: "likely_recoverable",
            candidate_strategy: "retry",
            approved_strategy: "retry",
            decision_status: "approved",
            rationale: [{ rule_id: "fraud_signal", outcome: "passed", reason_code: null }],
            scheduled_not_before: "2026-08-28T20:00:00Z",
            decision_engine_version: "1",
            created_at: "2026-08-28T14:05:00Z",
          },
        },
      ],
      "/risk/payments": [200, PAYMENTS],
    });
    render(await RecoveryCaseDetailPage(detailProps(CASES[0].id)));

    expect(screen.getByText("4 · Decision")).toBeInTheDocument();
    expect(screen.getByText(/^approved$/i)).toBeInTheDocument();
    expect(screen.getByText("fraud_signal")).toBeInTheDocument();
    // No "Decide" button once a decision already exists.
    expect(screen.queryByRole("button", { name: /decide/i })).not.toBeInTheDocument();
  });

  it("offers the Decide action for a diagnosed case with no decision yet", async () => {
    stubRoutes({
      "/recovery/cases/": [200, { ...CASES[0], state: "diagnosed", history: [], decision: null }],
      "/risk/payments": [200, PAYMENTS],
    });
    render(await RecoveryCaseDetailPage(detailProps(CASES[0].id)));

    expect(screen.getByRole("button", { name: /decide/i })).toBeInTheDocument();
  });
});
