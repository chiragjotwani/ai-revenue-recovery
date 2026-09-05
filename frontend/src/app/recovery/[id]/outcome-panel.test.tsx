import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OutcomePanel } from "./outcome-panel";
import type { Outcome } from "@/lib/api";
import type { OutcomeActionState } from "./outcome-action";

const { observeOutcomeMock } = vi.hoisted(() => ({ observeOutcomeMock: vi.fn() }));

vi.mock("./outcome-action", () => ({
  observeOutcome: observeOutcomeMock,
}));

function makeOutcome(overrides: Partial<Outcome> = {}): Outcome {
  return {
    id: "o-1",
    case_id: "c-1",
    action_id: "a-1",
    attempt_no: 1,
    outcome: "unresolved",
    is_terminal: false,
    evidence_payment_id: null,
    created_at: "2026-03-01T00:00:00Z",
    ...overrides,
  };
}

describe("OutcomePanel: never implies recovery merely from execution", () => {
  it("shows an in-progress message, not a recovered claim, before any observation", () => {
    render(
      <OutcomePanel
        caseId="c-1"
        caseState="action_executed"
        actionStatus="executed"
        initialOutcome={null}
        closedAt={null}
      />,
    );
    expect(screen.getByText(/in progress/i)).toBeInTheDocument();
    expect(screen.queryByText(/^recovered$/i)).not.toBeInTheDocument();
  });

  it("offers Observe outcome only once the action has executed", () => {
    render(
      <OutcomePanel
        caseId="c-1"
        caseState="action_scheduled"
        actionStatus="scheduled"
        initialOutcome={null}
        closedAt={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /observe outcome/i })).not.toBeInTheDocument();
  });

  it("renders recovered distinctly, only when evidence supports it", () => {
    render(
      <OutcomePanel
        caseId="c-1"
        caseState="recovered"
        actionStatus="executed"
        initialOutcome={makeOutcome({
          outcome: "recovered",
          is_terminal: true,
          evidence_payment_id: "p-later",
        })}
        closedAt="2026-03-01T02:00:00Z"
      />,
    );
    expect(screen.getByText(/^recovered$/i)).toBeInTheDocument();
    expect(screen.getByText(/later successful payment was observed/i)).toBeInTheDocument();
  });

  it("renders not_recovered honestly, without implying the case is closed", () => {
    render(
      <OutcomePanel
        caseId="c-1"
        caseState="observing"
        actionStatus="executed"
        initialOutcome={makeOutcome({
          outcome: "not_recovered",
          evidence_payment_id: "p-fail",
        })}
        closedAt={null}
      />,
    );
    expect(screen.getByText(/^not recovered$/i)).toBeInTheDocument();
    expect(screen.getByText(/not yet terminal/i)).toBeInTheDocument();
  });
});

describe("OutcomePanel: observe interaction", () => {
  it("shows a loading state while pending", async () => {
    let resolveAction!: (v: OutcomeActionState) => void;
    observeOutcomeMock.mockReturnValue(
      new Promise<OutcomeActionState>((resolve) => {
        resolveAction = resolve;
      }),
    );

    render(
      <OutcomePanel
        caseId="c-1"
        caseState="action_executed"
        actionStatus="executed"
        initialOutcome={null}
        closedAt={null}
      />,
    );
    const button = screen.getByRole("button", { name: /observe outcome/i });
    fireEvent.click(button);

    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());

    resolveAction({ status: "success", outcome: makeOutcome() });
    await waitFor(() => expect(screen.getByText(/^unresolved$/i)).toBeInTheDocument());
  });

  type ErrorKind = Extract<OutcomeActionState, { status: "error" }>["kind"];

  it.each<[ErrorKind, string]>([
    ["not_found", "no longer exists"],
    ["conflict", "action has executed"],
    ["unavailable", "did not respond"],
  ])("shows the %s error inline without crashing the panel", async (kind, expectedText) => {
    observeOutcomeMock.mockResolvedValue({ status: "error", kind, message: `x ${expectedText} x` });

    render(
      <OutcomePanel
        caseId="c-1"
        caseState="action_executed"
        actionStatus="executed"
        initialOutcome={null}
        closedAt={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /observe outcome/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expectedText);
    expect(screen.getByRole("button", { name: /observe outcome/i })).toBeInTheDocument();
  });
});
