import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecidePanel } from "./decide-panel";
import type { Decision, DecisionStatus } from "@/lib/api";
import type { DecideActionState } from "./decide-action";

const { decideCaseMock } = vi.hoisted(() => ({ decideCaseMock: vi.fn() }));

vi.mock("./decide-action", () => ({
  decideCase: decideCaseMock,
}));

function makeDecision(overrides: Partial<Decision> = {}): Decision {
  return {
    id: "d-1",
    case_id: "c-1",
    diagnosis_id: "diag-1",
    recoverability: "likely_recoverable",
    candidate_strategy: "retry",
    approved_strategy: "retry",
    decision_status: "approved",
    rationale: [{ rule_id: "already_paid", outcome: "not_applicable", reason_code: null }],
    scheduled_not_before: "2026-03-01T06:00:00Z",
    decision_engine_version: "1",
    created_at: "2026-03-01T00:00:00Z",
    ...overrides,
  };
}

describe("DecidePanel: decision states", () => {
  it.each<[DecisionStatus, RegExp]>([
    ["approved", /^approved$/i],
    ["rejected", /^rejected$/i],
    ["escalated", /^escalated$/i],
    ["superseded", /^superseded$/i],
  ])("renders the %s decision status clearly, not as an error", (status, matcher) => {
    render(
      <DecidePanel
        caseId="c-1"
        caseState="decision_pending"
        initialDecision={makeDecision({ decision_status: status })}
      />,
    );
    expect(screen.getByText(matcher)).toBeInTheDocument();
    // Escalated/rejected are valid business outcomes, never rendered as an
    // alert/error state.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a strategy downgrade distinctly from a straight approval", () => {
    render(
      <DecidePanel
        caseId="c-1"
        caseState="decision_pending"
        initialDecision={makeDecision({
          candidate_strategy: "retry",
          approved_strategy: "manual_review",
        })}
      />,
    );
    expect(screen.getByText(/downgraded/i)).toBeInTheDocument();
  });

  it("renders structured rationale entries, never free-text AI reasoning", () => {
    render(
      <DecidePanel
        caseId="c-1"
        caseState="decision_pending"
        initialDecision={makeDecision({
          rationale: [
            { rule_id: "fraud_signal", outcome: "failed", reason_code: "suspected_fraud" },
          ],
        })}
      />,
    );
    expect(screen.getByText("fraud_signal")).toBeInTheDocument();
    expect(screen.getByText(/suspected fraud/i)).toBeInTheDocument();
  });
});

describe("DecidePanel: pre-decision states", () => {
  it("offers the Decide action only for a diagnosed case", () => {
    render(<DecidePanel caseId="c-1" caseState="diagnosed" initialDecision={null} />);
    expect(screen.getByRole("button", { name: /decide/i })).toBeInTheDocument();
  });

  it("does not offer the Decide action before diagnosis", () => {
    render(<DecidePanel caseId="c-1" caseState="detected" initialDecision={null} />);
    expect(screen.queryByRole("button", { name: /decide/i })).not.toBeInTheDocument();
    expect(screen.getByText(/not been diagnosed yet/i)).toBeInTheDocument();
  });
});

describe("DecidePanel: decide interaction", () => {
  it("shows a loading state while the action is pending", async () => {
    let resolveAction!: (v: DecideActionState) => void;
    decideCaseMock.mockReturnValue(
      new Promise<DecideActionState>((resolve) => {
        resolveAction = resolve;
      }),
    );

    render(<DecidePanel caseId="c-1" caseState="diagnosed" initialDecision={null} />);
    const button = screen.getByRole("button", { name: /decide/i });
    fireEvent.click(button);

    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());

    resolveAction({ status: "success", decision: makeDecision() });
    await waitFor(() => expect(screen.getByText(/^approved$/i)).toBeInTheDocument());
  });

  it("renders the decision returned by a successful decide call", async () => {
    decideCaseMock.mockResolvedValue({ status: "success", decision: makeDecision() });

    render(<DecidePanel caseId="c-1" caseState="diagnosed" initialDecision={null} />);
    fireEvent.click(screen.getByRole("button", { name: /decide/i }));

    await waitFor(() => expect(screen.getByText(/^approved$/i)).toBeInTheDocument());
  });

  type ErrorKind = Extract<DecideActionState, { status: "error" }>["kind"];

  it.each<[ErrorKind, string]>([
    ["not_found", "no longer exists"],
    ["conflict", "not in a decidable state"],
    ["invalid", "invalid"],
    ["unavailable", "did not respond"],
  ])(
    "shows the %s error inline without crashing the panel",
    async (kind, expectedText) => {
      decideCaseMock.mockResolvedValue({
        status: "error",
        kind,
        message: `x ${expectedText} x`,
      });

      render(<DecidePanel caseId="c-1" caseState="diagnosed" initialDecision={null} />);
      fireEvent.click(screen.getByRole("button", { name: /decide/i }));

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(expectedText);
      // The Decide action remains available to retry.
      expect(screen.getByRole("button", { name: /decide/i })).toBeInTheDocument();
    },
  );

  it("duplicate decide calls (double click) still resolve to one rendered decision", async () => {
    decideCaseMock.mockResolvedValue({ status: "success", decision: makeDecision() });

    render(<DecidePanel caseId="c-1" caseState="diagnosed" initialDecision={null} />);
    const button = screen.getByRole("button", { name: /decide/i });
    fireEvent.click(button);
    fireEvent.click(button); // disabled while pending; a no-op if it fires again

    await waitFor(() => expect(screen.getByText(/^approved$/i)).toBeInTheDocument());
    expect(screen.getAllByText(/^approved$/i)).toHaveLength(1);
  });
});
